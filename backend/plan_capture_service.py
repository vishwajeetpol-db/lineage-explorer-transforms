"""Runtime Plan Capture — app-side read path.

Gated by the `lineage_tracking.plan_capture` feature flag
(backend/feature_flags.py). This module NEVER triggers a capture itself —
capture only happens inside a Lakeflow Job/Pipeline that has the vendored
backend/plan_capture wheel/module installed and calls
`plan_capture.capture(df, target)` before a write (see
docs/capabilites.md for the opt-in steps). This module only *reads* what those
jobs already wrote, and only when the flag is on.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.feature_flags import get_flag_state
from backend.plan_capture.plan_parser import parse_plan

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
CAPTURED_PLANS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.captured_plans"
CAPTURED_CDC_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.captured_cdc_specs"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")


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


def get_plan_capture_status() -> dict:
    """Guarded status probe for the Control Panel card — never raises."""
    enabled = get_flag_state("lineage_tracking.plan_capture")
    status = {
        "enabled": enabled,
        "table_reachable": False,
        "captured_plan_count": 0,
        "captured_cdc_spec_count": 0,
        "distinct_targets": 0,
    }
    if not enabled:
        return status
    try:
        rows = _execute_sql(
            f"SELECT count(*) AS n, count(DISTINCT target_full_name) AS t FROM {CAPTURED_PLANS_TABLE}"
        )
        if rows:
            status["captured_plan_count"] = int(rows[0]["n"] or 0)
            status["distinct_targets"] = int(rows[0]["t"] or 0)
        status["table_reachable"] = True
    except Exception as e:
        logger.info(f"plan_capture_service: captured_plans not reachable yet: {e}")
    try:
        rows = _execute_sql(f"SELECT count(*) AS n FROM {CAPTURED_CDC_TABLE}")
        if rows:
            status["captured_cdc_spec_count"] = int(rows[0]["n"] or 0)
    except Exception:
        pass  # CDC spec table is optional — absence is not an error
    return status


def get_captured_expression(catalog: str, schema: str, table: str, column: str) -> Optional[dict]:
    """Best-effort: latest captured plan for the target table, parsed, matched to
    `column`. Returns None (never raises) when the flag is off, the table is
    unreachable, or the column isn't present in the captured plan.

    Callers MUST validate catalog/schema/table/column (e.g. via
    backend.main._validate_identifier) before calling this — these values are
    interpolated into SQL, matching the convention used throughout
    lineage_service.py / transform_service.py.
    """
    if not get_flag_state("lineage_tracking.plan_capture"):
        return None
    target = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(
            f"SELECT analyzed_plan, version, captured_via, captured_at "
            f"FROM {CAPTURED_PLANS_TABLE} "
            f"WHERE target_full_name = '{target}' "
            f"ORDER BY version DESC LIMIT 1"
        )
        if not rows:
            return None
        parsed = parse_plan(rows[0]["analyzed_plan"])
        match = next((c for c in parsed if c.get("target_column") == column), None)
        if not match:
            return None
        return {
            **match,
            "captured_via": rows[0].get("captured_via"),
            "captured_at": str(rows[0].get("captured_at")) if rows[0].get("captured_at") else None,
            "version": rows[0].get("version"),
        }
    except Exception as e:
        logger.info(f"plan_capture_service: no captured expression for {target}.{column}: {e}")
        return None
