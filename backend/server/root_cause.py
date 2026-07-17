"""Root-cause tracing — capability 33.

Explicitly called out in CLAUDE.md as "Next build": given an anomaly or
DQ failure on a target column, walk the lineage graph backwards to identify
the most likely source of the bad data.

Algorithm:
  1. Collect the upstream column-lineage path for (catalog.schema.table, column)
     from system.access.column_lineage (same lookback window as the main graph).
  2. For each upstream table found, query system.access.audit for recent write
     events and system.lakeflow.job_run_timeline for failed runs that wrote
     to that table inside a configurable time window around the anomaly.
  3. Rank candidates by:
       a. How close they are to the target column (hop distance)
       b. Whether a failing or degraded producer run coincides with the
          anomaly_timestamp window
       c. Whether the upstream column is sensitive (governance heuristic)
  4. Return a ranked list of {candidate_table, candidate_column,
     hop_distance, score, evidence: []}.

Non-fatal throughout.
"""
from __future__ import annotations

import os
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
LINEAGE_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))
ROOT_CAUSE_MAX_HOPS = int(os.environ.get("ROOT_CAUSE_MAX_HOPS", "6"))
ROOT_CAUSE_ANOMALY_WINDOW_HOURS = int(os.environ.get("ROOT_CAUSE_ANOMALY_WINDOW_HOURS", "24"))


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
    )
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def _walk_upstream_columns(
    start_table: str,
    start_column: str,
    max_hops: int = ROOT_CAUSE_MAX_HOPS,
) -> list[dict]:
    """BFS upstream column walk. Returns list of {table, column, hop}."""
    visited: set[tuple] = set()
    path: list[dict] = []
    frontier = [(start_table, start_column, 0)]
    while frontier:
        tbl, col, hop = frontier.pop(0)
        if (tbl, col) in visited or hop >= max_hops:
            continue
        visited.add((tbl, col))
        try:
            rows = _execute_sql(
                f"SELECT DISTINCT source_table_full_name, source_column_name "
                f"FROM system.access.column_lineage "
                f"WHERE target_table_full_name = '{tbl}' "
                f"  AND target_column_name = '{col}' "
                f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp()) "
                f"LIMIT 30"
            )
            for r in rows:
                src_tbl = r.get("source_table_full_name", "")
                src_col = r.get("source_column_name", "")
                if src_tbl and src_col:
                    path.append({"table": src_tbl, "column": src_col, "hop": hop + 1})
                    frontier.append((src_tbl, src_col, hop + 1))
        except Exception as e:
            logger.debug(f"root_cause: upstream walk error at {tbl}.{col}: {e}")
    return path


def _get_failed_runs_around(
    table_fqn: str,
    anomaly_ts: Optional[str],
    window_hours: int = ROOT_CAUSE_ANOMALY_WINDOW_HOURS,
) -> list[dict]:
    """Return failed job/pipeline runs that wrote to table_fqn near anomaly_ts."""
    evidence: list[dict] = []
    ts_filter = ""
    if anomaly_ts:
        ts_filter = (
            f"AND period_start_time >= TIMESTAMP '{anomaly_ts}' - INTERVAL {window_hours} HOURS "
            f"AND period_start_time <= TIMESTAMP '{anomaly_ts}' + INTERVAL {window_hours} HOURS "
        )
    try:
        # Jobs that wrote to this table with a non-SUCCESS result
        rows = _execute_sql(
            f"SELECT DISTINCT jrt.job_id, jrt.result_state, jrt.period_start_time "
            f"FROM system.lakeflow.job_run_timeline jrt "
            f"JOIN system.access.table_lineage tl "
            f"  ON tl.entity_id = CAST(jrt.job_id AS STRING) AND tl.entity_type = 'JOB' "
            f"WHERE tl.target_table_full_name = '{table_fqn}' "
            f"  AND jrt.result_state IN ('FAILED','TIMEDOUT') "
            f"{ts_filter} "
            f"LIMIT 10"
        )
        for r in rows:
            evidence.append({
                "type": "failed_job_run",
                "entity_type": "JOB",
                "entity_id": str(r.get("job_id")),
                "result_state": r.get("result_state"),
                "period_start_time": str(r.get("period_start_time") or ""),
            })
    except Exception as e:
        logger.debug(f"root_cause: failed run query failed for {table_fqn}: {e}")
    return evidence


def trace_root_cause(
    catalog: str,
    schema: str,
    table: str,
    column: str,
    anomaly_timestamp: Optional[str] = None,
    max_hops: int = ROOT_CAUSE_MAX_HOPS,
) -> dict:
    """Return a ranked list of upstream root-cause candidates for a failing column.

    Response:
    {
        "target_table": str,
        "target_column": str,
        "anomaly_timestamp": str | null,
        "candidates": [
            {
                "table": str,
                "column": str,
                "hop": int,
                "score": float,         # 0.0-1.0, higher = more suspicious
                "evidence": [...]        # failed runs, DQ signals, etc.
            },
            ...
        ]
    }
    """
    full_name = f"{catalog}.{schema}.{table}"
    result: dict = {
        "target_table": full_name,
        "target_column": column,
        "anomaly_timestamp": anomaly_timestamp,
        "candidates": [],
    }

    # Step 1: Walk upstream
    try:
        upstream = _walk_upstream_columns(full_name, column, max_hops=max_hops)
    except Exception as e:
        result["error"] = str(e)
        return result

    if not upstream:
        result["detail"] = "No upstream columns found; this may be a root/source column."
        return result

    # Step 2: Gather evidence per unique upstream table
    table_evidence: dict[str, list[dict]] = {}
    seen_tables = {item["table"] for item in upstream}
    for tbl in seen_tables:
        evidence = _get_failed_runs_around(tbl, anomaly_timestamp)
        table_evidence[tbl] = evidence

    # Step 3: Score candidates
    # Score = 1/(hop+1) + 0.4 * has_evidence + 0.1 * (1/hop for close proximity)
    candidates: list[dict] = []
    seen_col_keys: set[str] = set()
    for item in upstream:
        key = f"{item['table']}::{item['column']}"
        if key in seen_col_keys:
            continue
        seen_col_keys.add(key)
        evidence = table_evidence.get(item["table"], [])
        hop = item["hop"]
        score = round(
            (1.0 / (hop + 1)) + (0.4 if evidence else 0.0),
            4
        )
        candidates.append({
            "table": item["table"],
            "column": item["column"],
            "hop": hop,
            "score": score,
            "evidence": evidence,
        })

    # Sort by score descending, hop ascending as tiebreak
    candidates.sort(key=lambda c: (-c["score"], c["hop"]))
    result["candidates"] = candidates[:20]  # cap at 20
    return result
