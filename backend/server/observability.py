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
OBS_LOOKBACK_DAYS = int(os.environ.get("OBSERVABILITY_LOOKBACK_DAYS", "90"))


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
        # NOTE: pipeline_update_timeline uses period_start_time/period_end_time and
        # result_state (NOT update_start_time/state — those columns don't exist).
        rows = _execute_sql(
            f"SELECT "
            f"  COUNT(*) AS total_updates, "
            f"  SUM(CASE WHEN result_state = 'COMPLETED' THEN 1 ELSE 0 END) AS successful_updates, "
            f"  SUM(CASE WHEN result_state IN ('FAILED','CANCELED') THEN 1 ELSE 0 END) AS failed_updates, "
            f"  MAX(period_start_time) AS last_update_at, "
            f"  AVG(DATEDIFF(SECOND, period_start_time, period_end_time)) AS avg_duration_seconds "
            f"FROM system.lakeflow.pipeline_update_timeline "
            f"WHERE pipeline_id = '{pipeline_id}' "
            f"  AND period_start_time >= dateadd(DAY, -{OBS_LOOKBACK_DAYS}, current_timestamp())"
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
            f"SELECT result_state FROM system.lakeflow.pipeline_update_timeline "
            f"WHERE pipeline_id = '{pipeline_id}' ORDER BY period_start_time DESC LIMIT 1"
        )
        if last_rows:
            base["last_update_state"] = last_rows[0].get("result_state")
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


def _workspace_host() -> str:
    """Workspace base URL for run deep-links (best-effort)."""
    try:
        return _get_client().config.host.rstrip("/")
    except Exception:
        return ""


def _run_health_verdict(rows: list[dict], success_states: set[str]) -> str:
    """healthy/degraded/failing from a set of per-run rows."""
    total = len(rows)
    if total == 0:
        return "unknown"
    ok = sum(1 for r in rows if (r.get("result_state") in success_states))
    return _health_badge(ok / total, total)


def get_recent_runs(entity_type: str, entity_id: str, limit: int = 5) -> dict:
    """Return the last `limit` runs/updates for a JOB or PIPELINE with per-run
    status, duration, cost, failure reason, trigger, and a deep link.

    Cost is joined per-run from system.billing.usage via usage_metadata.job_run_id
    (jobs) / dlt_update_id (pipelines) — so unlike the rolling 30-day node badge,
    this is the actual spend of each individual run. Everything is best-effort:
    a missing privilege on billing just yields null costs, not an error.
    """
    et = (entity_type or "").upper()
    host = _workspace_host()
    out: dict = {
        "entity_type": et,
        "entity_id": entity_id,
        "lookback_days": OBS_LOOKBACK_DAYS,
        "runs": [],
        "verdict": "unknown",
        "success_rate": None,
        "avg_duration_seconds": None,
        "duration_trend": None,   # "up" | "down" | "flat" — latest vs prior avg
        "total_cost_usd": None,
        "cost_spike_run_id": None,
        "entity_url": None,
    }
    if et not in ("JOB", "PIPELINE"):
        out["detail"] = "Recent runs only supported for JOB and PIPELINE."
        return out

    is_job = et == "JOB"
    timeline = "system.lakeflow.job_run_timeline" if is_job else "system.lakeflow.pipeline_update_timeline"
    id_col = "job_id" if is_job else "pipeline_id"
    run_id_col = "run_id" if is_job else "update_id"
    success_states = {"SUCCEEDED"} if is_job else {"COMPLETED"}
    out["entity_url"] = (f"{host}/#job/{entity_id}" if is_job
                         else f"{host}/#joblist/pipelines/{entity_id}") if host else None
    safe_id = entity_id.replace("'", "''")

    # 1) Per-run timeline rows (newest first). A single run/update spans multiple
    # timeline rows (one per state transition), so group by run id only and take
    # the terminal (last non-null) state via MAX_BY on end time.
    try:
        rows = _execute_sql(
            f"SELECT {run_id_col} AS run_id, "
            f"  MAX_BY(result_state, period_end_time) FILTER (WHERE result_state IS NOT NULL) AS result_state, "
            f"  MIN(period_start_time) AS started_at, MAX(period_end_time) AS ended_at, "
            f"  DATEDIFF(SECOND, MIN(period_start_time), MAX(period_end_time)) AS duration_seconds "
            f"FROM {timeline} "
            f"WHERE {id_col} = '{safe_id}' "
            f"  AND period_start_time >= dateadd(DAY, -{OBS_LOOKBACK_DAYS}, current_timestamp()) "
            f"GROUP BY {run_id_col} "
            f"ORDER BY started_at DESC "
            f"LIMIT {int(limit)}"
        )
    except Exception as e:
        logger.info(f"observability: recent runs unavailable for {entity_id}: {e}")
        return out

    # 2) Per-run cost from billing (best-effort; keyed by run/update id).
    cost_col = "usage_metadata.job_run_id" if is_job else "usage_metadata.dlt_update_id"
    cost_by_run: dict[str, float] = {}
    run_ids = [str(r.get("run_id")) for r in rows if r.get("run_id") is not None]
    if run_ids:
        in_list = ", ".join(f"'{rid.replace(chr(39), '')}'" for rid in run_ids)
        try:
            crows = _execute_sql(
                f"SELECT {cost_col} AS run_id, "
                f"  ROUND(SUM(u.usage_quantity * lp.pricing.effective_list.default), 2) AS cost_usd "
                f"FROM system.billing.usage u "
                f"JOIN system.billing.list_prices lp "
                f"  ON u.sku_name = lp.sku_name AND u.usage_unit = lp.usage_unit "
                f"  AND u.usage_start_time >= lp.price_start_time "
                f"  AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time) "
                f"WHERE {cost_col} IN ({in_list}) "
                f"  AND u.usage_date > current_date() - INTERVAL {OBS_LOOKBACK_DAYS} DAYS "
                f"GROUP BY {cost_col}"
            )
            for cr in crows:
                if cr.get("run_id") is not None:
                    cost_by_run[str(cr["run_id"])] = float(cr.get("cost_usd") or 0.0)
        except Exception as e:
            logger.info(f"observability: per-run cost unavailable for {entity_id}: {e}")

    runs: list[dict] = []
    for r in rows:
        rid = str(r.get("run_id")) if r.get("run_id") is not None else None
        state = r.get("result_state")
        runs.append({
            "run_id": rid,
            "result_state": state,
            "succeeded": state in success_states,
            "started_at": str(r.get("started_at")) if r.get("started_at") else None,
            "ended_at": str(r.get("ended_at")) if r.get("ended_at") else None,
            "duration_seconds": int(r["duration_seconds"]) if r.get("duration_seconds") is not None else None,
            "cost_usd": cost_by_run.get(rid) if rid else None,
            "run_url": (f"{host}/#job/{entity_id}/run/{rid}" if is_job and host and rid else out["entity_url"]),
        })
    out["runs"] = runs

    # 3) Summary — verdict, success rate, duration trend, cost total + spike.
    out["verdict"] = _run_health_verdict(rows, success_states)
    total = len(runs)
    if total:
        ok = sum(1 for r in runs if r["succeeded"])
        out["success_rate"] = round(ok / total, 4)
        durs = [r["duration_seconds"] for r in runs if r["duration_seconds"] is not None]
        if durs:
            out["avg_duration_seconds"] = round(sum(durs) / len(durs), 1)
            # Trend: latest run vs the average of the prior runs.
            if len(durs) >= 2 and runs[0]["duration_seconds"] is not None:
                prior = durs[1:]
                prior_avg = sum(prior) / len(prior)
                latest = runs[0]["duration_seconds"]
                if prior_avg > 0:
                    if latest > prior_avg * 1.25:
                        out["duration_trend"] = "up"
                    elif latest < prior_avg * 0.75:
                        out["duration_trend"] = "down"
                    else:
                        out["duration_trend"] = "flat"
        costs = [(r["run_id"], r["cost_usd"]) for r in runs if r["cost_usd"] is not None]
        if costs:
            out["total_cost_usd"] = round(sum(c for _, c in costs), 2)
            # Spike = a run costing >2x the median of the others.
            vals = sorted(c for _, c in costs)
            median = vals[len(vals) // 2]
            if median > 0:
                for rid, c in costs:
                    if c > median * 2:
                        out["cost_spike_run_id"] = rid
                        break
    return out


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
