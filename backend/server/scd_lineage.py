"""SCD/CDC lineage visualization — capability 34.

Special graph rendering for APPLY CHANGES (Auto CDC) and SCD Type 1 & 2
targets captured via capture_cdc_spec() (see backend/plan_capture/).  Shows
keys, sequence_by, dedup logic, and SCD type as first-class graph annotations
instead of plain pass-through edges.

This module reads the captured_cdc_specs table (written by capture_cdc_spec()
calls in user pipelines) and returns a structured CDC descriptor that the
frontend can render as annotated graph nodes and edges.
"""
from __future__ import annotations

import os
import json
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
CAPTURED_CDC_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.captured_cdc_specs"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")


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


def get_cdc_spec(catalog: str, schema: str, table: str) -> Optional[dict]:
    """Return the latest captured CDC spec for `catalog.schema.table`, or None.

    Returns a CDC descriptor dict:
    {
        "target":       str,   # catalog.schema.table
        "source":       str,   # catalog.schema.cdc_source
        "keys":         list[str],
        "sequence_by":  str,
        "scd_type":     int,   # 1 or 2
        "stored_as":    str,   # SCD_TYPE_1 | SCD_TYPE_2
        "ignore_null_updates": bool,
        "apply_as_deletes":    str | None,
        "apply_as_truncates":  str | None,
        "column_list":  list[str] | None,
        "except_column_list": list[str] | None,
        "captured_at":  str,
        "version":      int,
        "graph_annotations": [
            {"type": "key",         "columns": [...], "label": "Join keys"},
            {"type": "sequence_by", "column": str,    "label": "Sequence / ordering"},
            {"type": "scd_type",    "value": int,     "label": "SCD Type N"},
            ...
        ]
    }
    """
    full_name = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(
            f"SELECT * FROM {CAPTURED_CDC_TABLE} "
            f"WHERE target_full_name = '{full_name}' "
            f"ORDER BY version DESC LIMIT 1"
        )
        if not rows:
            return None
        r = rows[0]
        # Parse JSON columns (keys, column_list, etc.)
        def _parse_json_col(val):
            if val is None:
                return None
            if isinstance(val, list):
                return val
            try:
                return json.loads(val)
            except Exception:
                return [val]

        keys = _parse_json_col(r.get("keys")) or []
        scd_type = int(r.get("scd_type") or 1)
        sequence_by = r.get("sequence_by") or ""
        col_list = _parse_json_col(r.get("column_list"))
        except_col_list = _parse_json_col(r.get("except_column_list"))

        # Build first-class graph annotations
        annotations = []
        if keys:
            annotations.append({"type": "key", "columns": keys, "label": f"Join key{'s' if len(keys) > 1 else ''}: {', '.join(keys)}"})
        if sequence_by:
            annotations.append({"type": "sequence_by", "column": sequence_by, "label": f"Ordered by: {sequence_by}"})
        annotations.append({"type": "scd_type", "value": scd_type, "label": f"SCD Type {scd_type}"})
        if r.get("ignore_null_updates"):
            annotations.append({"type": "behavior", "value": "ignore_null_updates", "label": "Null updates ignored"})
        if r.get("apply_as_deletes"):
            annotations.append({"type": "behavior", "value": "apply_as_deletes", "label": f"Deletes when: {r['apply_as_deletes']}"})
        if col_list:
            annotations.append({"type": "column_scope", "columns": col_list, "label": f"Tracked columns: {len(col_list)}"})
        if except_col_list:
            annotations.append({"type": "column_scope_except", "columns": except_col_list, "label": f"Excluded columns: {len(except_col_list)}"})

        return {
            "target": full_name,
            "source": r.get("source_full_name") or "",
            "keys": keys,
            "sequence_by": sequence_by,
            "scd_type": scd_type,
            "stored_as": f"SCD_TYPE_{scd_type}",
            "ignore_null_updates": bool(r.get("ignore_null_updates")),
            "apply_as_deletes": r.get("apply_as_deletes"),
            "apply_as_truncates": r.get("apply_as_truncates"),
            "column_list": col_list,
            "except_column_list": except_col_list,
            "captured_at": str(r.get("captured_at") or ""),
            "version": int(r.get("version") or 0),
            "graph_annotations": annotations,
        }
    except Exception as e:
        logger.info(f"scd_lineage: could not fetch CDC spec for {full_name}: {e}")
        return None


def list_cdc_targets() -> list[dict]:
    """Return all tables that have captured CDC specs (sorted by most recent)."""
    try:
        rows = _execute_sql(
            f"SELECT target_full_name, source_full_name, scd_type, MAX(version) AS latest_version, "
            f"       MAX(captured_at) AS latest_captured_at "
            f"FROM {CAPTURED_CDC_TABLE} "
            f"GROUP BY target_full_name, source_full_name, scd_type "
            f"ORDER BY latest_captured_at DESC LIMIT 200"
        )
        return rows
    except Exception as e:
        logger.info(f"scd_lineage: could not list CDC targets: {e}")
        return []
