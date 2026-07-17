"""Observability (run health) — capability 19.

Queries system.lakeflow.job_run_timeline and pipeline_update_timeline to
compute per-entity success rate, failure counts, last run timestamp, and a
simple health badge (healthy / degraded / failing).

All functions are non-fatal: degraded results are returned rather than
raising, so the lineage graph never breaks because observability is
unavailable (e.g., missing system.lakeflow privilege).
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
# How many days back to aggregate run health. A wider window smooths noise;
# a narrower window surfaces recent regressions faster.
OBS_LOOKBACK_DAYS = int(os.environ.get("OBSERVABILITY_LOOKBACK_DAYS", "30"))


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")
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


def _health_badge(success_rate: float, total_runs: int) -> str:
    """Return 'healthy' | 'degraded' | 'failing' | 'unknown'."""
    if total_runs == 0:
        return "unknown"
    if success_rate >= 0.90:
        return "healthy"
    if success_rate >= 0.70:
        return "degraded"
    return "failing"


# ---------------------------------------------------------------------------
# Job run health (system.lakeflow.job_run_timeline)
# ---------------------------------------------------------------------------

def get_job_run_health(job_id: str) -> dict:
    """Return run health stats for a specific Lakeflow Job."""
    base: dict = {
        "entity_type": "JOB",
        "entity_id": job_id,
        "total_runs": 0,
        "successful_runs": 0,
        "failed_runs": 0,
        "success_rate": None,
        "last_run_at": None,
        "last_run_result": None,
        "avg_duration_seconds": None,
        "health_badge": "unknown",
        "lookback_days": OBS_LOOKBACK_DAYS,
    }
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  COUNT(*) AS total_runs, "
            f"  SUM(CASE WHEN result_state = 'SUCCEEDED' THEN 1 ELSE 0 END) AS successful_runs, "
            f"  SUM(CASE WHEN result_state IN ('FAILED','TIMEDOUT','CANCELED') THEN 1 ELSE 0 END) AS failed_runs, "
            f"  MAX(period_start_time) AS last_run_at, "
            f"  AVG(DATEDIFF(SECOND, period_start_time, period_end_time)) AS avg_duration_seconds "
            f"FROM system.lakeflow.job_run_timeline "
            f"WHERE job_id = '{job_id}' "
            f"  AND period_start_time >= dateadd(DAY, -{OBS_LOOKBACK_DAYS}, current_timestamp())"
        )
        if rows:
            r = rows[0]
            total = int(r["total_runs"] or 0)
            successful = int(r["successful_runs"] or 0)
            failed = int(r["failed_runs"] or 0)
            rate = (successful / total) if total > 0 else None
            base.update({
                "total_runs": total,
                "successful_runs": successful,
                "failed_runs": failed,
                "success_rate": round(rate, 4) if rate is not None else None,
                "last_run_at": str(r["last_run_at"]) if r.get("last_run_at") else None,
                "avg_duration_seconds": round(float(r["avg_duration_seconds"]), 1) if r.get("avg_duration_seconds") else None,
                "health_badge": _health_badge(rate or 0.0, total),
            })
        # Fetch last run result state separately
        last_rows = _execute_sql(
            f"SELECT result_state FROM system.lakeflow.job_run_timeline "
            f"WHERE job_id = '{job_id}' ORDER BY period_start_time DESC LIMIT 1"
        )
        if last_rows:
            base["last_run_result"] = last_rows[0].get("result_state")
    except Exception as e:
        logger.info(f"observability: job run health unavailable for {job_id}: {e}")
    return base


def get_pipeline_update_health(pipeline_id: str) -> dict:
    """Return update health stats for a Lakeflow Spark Declarative Pipeline."""
    base: dict = {
        "entity_type": "PIPELINE",
        "entity_id": pipeline_id,
        "total_updates": 0,
        "successful_updates": 0,
        "failed_updates": 0,
        "success_rate": None,
        "last_update_at": None,
        "last_update_state": None,
        "avg_duration_seconds": None,
        "health_badge": "unknown",
        "lookback_days": OBS_LOOKBACK_DAYS,
    }
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  COUNT(*) AS total_updates, "
            f"  SUM(CASE WHEN state = 'COMPLETED' THEN 1 ELSE 0 END) AS successful_updates, "
            f"  SUM(CASE WHEN state IN ('FAILED','CANCELED') THEN 1 ELSE 0 END) AS failed_updates, "
            f"  MAX(update_start_time) AS last_update_at, "
            f"  AVG(DATEDIFF(SECOND, update_start_time, update_end_time)) AS avg_duration_seconds "
            f"FROM system.lakeflow.pipeline_update_timeline "
            f"WHERE pipeline_id = '{pipeline_id}' "
            f"  AND update_start_time >= dateadd(DAY, -{OBS_LOOKBACK_DAYS}, current_timestamp())"
        )
        if rows:
            r = rows[0]
            total = int(r["total_updates"] or 0)
            successful = int(r["successful_updates"] or 0)
            failed = int(r["failed_updates"] or 0)
            rate = (successful / total) if total > 0 else None
            base.update({
                "total_updates": total,
                "successful_updates": successful,
                "failed_updates": failed,
                "success_rate": round(rate, 4) if rate is not None else None,
                "last_update_at": str(r["last_update_at"]) if r.get("last_update_at") else None,
                "avg_duration_seconds": round(float(r["avg_duration_seconds"]), 1) if r.get("avg_duration_seconds") else None,
                "health_badge": _health_badge(rate or 0.0, total),
            })
        last_rows = _execute_sql(
            f"SELECT state FROM system.lakeflow.pipeline_update_timeline "
            f"WHERE pipeline_id = '{pipeline_id}' ORDER BY update_start_time DESC LIMIT 1"
        )
        if last_rows:
            base["last_update_state"] = last_rows[0].get("state")
    except Exception as e:
        logger.info(f"observability: pipeline update health unavailable for {pipeline_id}: {e}")
    return base


def get_entity_health(entity_type: str, entity_id: str) -> dict:
    """Dispatch to the right health function by entity type."""
    et = entity_type.upper()
    if et == "JOB":
        return get_job_run_health(entity_id)
    if et == "PIPELINE":
        return get_pipeline_update_health(entity_id)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "health_badge": "unknown",
        "detail": "Observability not supported for this entity type.",
    }


def get_table_producer_health(catalog: str, schema: str, table: str) -> list[dict]:
    """Return health stats for every producer entity of `catalog.schema.table`
    by joining system.access.table_lineage with the run/update timelines.
    """
    full_name = f"{catalog}.{schema}.{table}"
    try:
        producers = _execute_sql(
            f"SELECT DISTINCT entity_type, entity_id "
            f"FROM system.access.table_lineage "
            f"WHERE target_table_full_name = '{full_name}' "
            f"  AND entity_type IN ('JOB', 'PIPELINE')"
        )
    except Exception as e:
        logger.info(f"observability: could not fetch producers for {full_name}: {e}")
        return []

    results: list[dict] = []
    for p in producers:
        health = get_entity_health(p["entity_type"], p["entity_id"])
        results.append(health)
    return results
