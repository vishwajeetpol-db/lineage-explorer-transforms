"""Root-cause tracing — capability 09.

Given an anomaly or DQ failure on a target column, walk the lineage graph
backwards to identify the most likely source of the bad data.

Algorithm:
  1. Collect the upstream column-lineage path for (catalog.schema.table, column)
     from system.access.column_lineage (same lookback window as the main graph).
  2. For each upstream table found, query system.access.audit for recent write
     events and system.lakeflow.job_run_timeline for failed runs that wrote
     to that table inside a configurable time window around the anomaly.
  3. Query the app-owned `dq_rules` table for any defined rules on upstream
     columns and check for violations (adds "dq_violation" evidence).
  4. Rank candidates by:
       a. How close they are to the target column (hop distance)
       b. Whether a failing or degraded producer run coincides with the
          anomaly_timestamp window
       c. Whether the upstream column has DQ rule violations
       d. Whether the upstream column is sensitive (governance heuristic)
  5. Return a ranked list of {candidate_table, candidate_column,
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
LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
DQ_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_rules"
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


def _get_dq_violations(
    table_fqn: str,
    column_name: Optional[str] = None,
    sample_size: int = 1000,
) -> list[dict]:
    """Query the app-owned dq_rules table for rules on a given table/column,
    then execute each rule against a sample to detect violations.

    Returns evidence entries of type "dq_violation" for any rule with a
    pass rate below 1.0.
    """
    evidence: list[dict] = []
    try:
        # Fetch rules for this table (optionally column-specific)
        col_filter = ""
        if column_name:
            col_filter = f"AND (column_name = '{column_name}' OR column_name IS NULL)"
        rules = _execute_sql(
            f"SELECT rule_id, column_name, rule_type, expression, severity "
            f"FROM {DQ_TABLE} "
            f"WHERE table_fqn = '{table_fqn}' {col_filter} "
            f"LIMIT 20"
        )
        if not rules:
            return evidence

        # Execute each rule against a sample of the table
        for rule in rules:
            expr = rule.get("expression", "")
            if not expr:
                continue
            try:
                # Count violations in a sample
                result = _execute_sql(
                    f"SELECT COUNT(*) AS total, "
                    f"SUM(CASE WHEN NOT ({expr}) THEN 1 ELSE 0 END) AS violations "
                    f"FROM (SELECT * FROM {table_fqn} LIMIT {sample_size})"
                )
                if result:
                    total = int(result[0].get("total") or 0)
                    violations = int(result[0].get("violations") or 0)
                    if violations > 0:
                        pass_rate = round((total - violations) / max(total, 1), 4)
                        evidence.append({
                            "type": "dq_violation",
                            "rule_id": rule.get("rule_id"),
                            "rule_type": rule.get("rule_type"),
                            "column": rule.get("column_name"),
                            "expression": expr,
                            "severity": rule.get("severity"),
                            "pass_rate": pass_rate,
                            "violations": violations,
                            "sample_size": total,
                        })
            except Exception as e:
                logger.debug(f"root_cause: DQ rule exec failed for {rule.get('rule_id')}: {e}")
    except Exception as e:
        logger.debug(f"root_cause: DQ rules query failed for {table_fqn}: {e}")
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
                "evidence": [...]        # failed runs, DQ violations, etc.
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

    # Step 2: Gather evidence per unique upstream table (failed runs)
    table_evidence: dict[str, list[dict]] = {}
    seen_tables = {item["table"] for item in upstream}
    for tbl in seen_tables:
        evidence = _get_failed_runs_around(tbl, anomaly_timestamp)
        table_evidence[tbl] = evidence

    # Step 3: Gather DQ violation evidence per upstream table+column
    dq_evidence: dict[str, list[dict]] = {}
    for item in upstream:
        key = f"{item['table']}::{item['column']}"
        if key not in dq_evidence:
            dq_evidence[key] = _get_dq_violations(item["table"], item["column"])

    # Step 4: Score candidates
    # Score = proximity + run_failure_signal + dq_violation_signal
    #   proximity:    1/(hop+1)        — closer = more suspicious
    #   run_failure:  +0.3 if failed runs found
    #   dq_violation: +0.3 if DQ rules violated (+ 0.1 if severity=ERROR)
    candidates: list[dict] = []
    seen_col_keys: set[str] = set()
    for item in upstream:
        key = f"{item['table']}::{item['column']}"
        if key in seen_col_keys:
            continue
        seen_col_keys.add(key)

        run_ev = table_evidence.get(item["table"], [])
        dq_ev = dq_evidence.get(key, [])
        all_evidence = run_ev + dq_ev
        hop = item["hop"]

        # Scoring
        proximity_score = 1.0 / (hop + 1)
        run_score = 0.3 if run_ev else 0.0
        dq_score = 0.0
        if dq_ev:
            dq_score = 0.3
            # Boost if any ERROR-severity violation
            if any(e.get("severity") == "ERROR" for e in dq_ev):
                dq_score = 0.4

        score = round(proximity_score + run_score + dq_score, 4)

        candidates.append({
            "table": item["table"],
            "column": item["column"],
            "hop": hop,
            "score": min(score, 1.0),
            "evidence": all_evidence,
        })

    # Sort by score descending, hop ascending as tiebreak
    candidates.sort(key=lambda c: (-c["score"], c["hop"]))
    result["candidates"] = candidates[:20]  # cap at 20
    return result


# ---------------------------------------------------------------------------
# Table-level root-cause TRACE (health-based, no column/anomaly required)
#
# This is the "auto-run" trace the workspace surfaces the moment a table is
# selected: walk upstream TABLES, find each table's producers, classify each
# producer's recent run health, and surface a prime suspect + failure path.
# ---------------------------------------------------------------------------

STALE_DAYS = int(os.environ.get("ROOT_CAUSE_STALE_DAYS", "7"))


def _walk_upstream_tables(start_table: str, max_hops: int = ROOT_CAUSE_MAX_HOPS) -> dict[str, int]:
    """BFS upstream over system.access.table_lineage. Returns {table: min_hop}."""
    visited: dict[str, int] = {}
    frontier = [start_table]
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        quoted = ", ".join(f"'{t}'" for t in frontier)
        try:
            rows = _execute_sql(
                f"SELECT DISTINCT source_table_full_name "
                f"FROM system.access.table_lineage "
                f"WHERE target_table_full_name IN ({quoted}) "
                f"  AND source_table_full_name IS NOT NULL "
                f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp())"
            )
        except Exception as e:
            logger.info(f"root_cause: upstream table walk hop {hop} failed: {e}")
            break
        nxt = []
        for r in rows:
            t = r.get("source_table_full_name")
            if t and t != start_table and t not in visited:
                visited[t] = hop
                nxt.append(t)
        frontier = nxt
    return visited


def _producers_for_table(table_fqn: str) -> list[dict]:
    """Return distinct producers (JOB/PIPELINE/etc) that wrote to table_fqn."""
    try:
        rows = _execute_sql(
            f"SELECT DISTINCT entity_type, entity_id "
            f"FROM system.access.table_lineage "
            f"WHERE target_table_full_name = '{table_fqn}' "
            f"  AND entity_type IS NOT NULL AND entity_id IS NOT NULL "
            f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp()) "
            f"LIMIT 20"
        )
        return [{"entity_type": r.get("entity_type"), "entity_id": str(r.get("entity_id"))} for r in rows]
    except Exception as e:
        logger.info(f"root_cause: producer lookup failed for {table_fqn}: {e}")
        return []


def _producer_health(entity_type: str, entity_id: str) -> dict:
    """Classify a producer's recent run health as failed | stale | healthy | no_history."""
    from backend.server.observability import get_job_run_health, get_pipeline_update_health

    et = (entity_type or "").upper()
    if et == "JOB":
        h = get_job_run_health(entity_id)
        total = h.get("total_runs", 0)
        last_result = h.get("last_run_result")
        last_at = h.get("last_run_at")
        success_rate = h.get("success_rate")
    elif et == "PIPELINE":
        h = get_pipeline_update_health(entity_id)
        total = h.get("total_updates", 0)
        last_result = h.get("last_update_state")
        last_at = h.get("last_update_at")
        success_rate = h.get("success_rate")
    else:
        return {"entity_type": et, "entity_id": entity_id, "status": "no_history",
                "last_result": None, "last_run_at": None, "success_rate": None}

    if total == 0:
        status = "no_history"
    elif last_result in ("FAILED", "TIMEDOUT", "CANCELED"):
        status = "failed"
    else:
        # Healthy last run — but is it stale? (no run within STALE_DAYS)
        status = "healthy"
        if last_at:
            try:
                dt = datetime.fromisoformat(str(last_at).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) - dt > timedelta(days=STALE_DAYS):
                    status = "stale"
            except Exception:
                pass
    return {
        "entity_type": et,
        "entity_id": entity_id,
        "status": status,
        "last_result": last_result,
        "last_run_at": str(last_at) if last_at else None,
        "success_rate": success_rate,
    }


# Severity order for ranking a table's overall status from its producers.
_STATUS_RANK = {"failed": 3, "stale": 2, "healthy": 1, "no_history": 0}


def trace_root_cause_table(catalog: str, schema: str, table: str,
                           max_hops: int = ROOT_CAUSE_MAX_HOPS) -> dict:
    """Health-based root-cause trace for a table (no column/anomaly needed).

    Walks upstream tables, classifies each table's producers by run health, then
    surfaces:
      - counts: failed / stale / healthy / no_history producer tables
      - prime_suspect: the worst-health table closest to the focus
      - failure_path: focus → prime suspect chain
      - flagged: per-table producer health detail (worst-first)
    """
    focus = f"{catalog}.{schema}.{table}"
    upstream = _walk_upstream_tables(focus, max_hops=max_hops)
    # Include the focus table itself (hop 0) so its own producer shows up.
    all_tables = {focus: 0, **upstream}

    flagged: list[dict] = []
    counts = {"failed": 0, "stale": 0, "healthy": 0, "no_history": 0}
    for tbl, hop in all_tables.items():
        producers = _producers_for_table(tbl)
        healths = [_producer_health(p["entity_type"], p["entity_id"]) for p in producers]
        # A table with no producers at all is a source/ingested table — skip counting.
        if not healths:
            continue
        worst = max(healths, key=lambda h: _STATUS_RANK.get(h["status"], 0))
        counts[worst["status"]] = counts.get(worst["status"], 0) + 1
        flagged.append({
            "table": tbl,
            "short_name": ".".join(tbl.split(".")[-2:]),
            "hop": hop,
            "is_focus": tbl == focus,
            "status": worst["status"],
            "producers": healths,
        })

    # Rank flagged tables: worst status first, then closest hop.
    flagged.sort(key=lambda f: (-_STATUS_RANK.get(f["status"], 0), f["hop"]))

    # Prime suspect = worst-status upstream table (exclude the focus itself if a
    # genuine upstream suspect exists) closest to the focus.
    suspects = [f for f in flagged if f["status"] in ("failed", "stale") and not f["is_focus"]]
    prime = suspects[0] if suspects else (flagged[0] if flagged else None)

    # Failure path: focus → prime suspect (short chain, by hop).
    failure_path: list[dict] = []
    if prime and not prime["is_focus"]:
        chain = [f for f in flagged if f["hop"] <= prime["hop"] and (f["is_focus"] or f["status"] in ("failed", "stale"))]
        chain.sort(key=lambda f: f["hop"])
        failure_path = [{"table": c["table"], "short_name": c["short_name"], "hop": c["hop"], "status": c["status"]} for c in chain]

    return {
        "focus_table": focus,
        "max_hops": max_hops,
        "lookback_days": LINEAGE_LOOKBACK_DAYS,
        "stale_days": STALE_DAYS,
        "counts": counts,
        "prime_suspect": prime,
        "failure_path": failure_path,
        "flagged": flagged,
    }
