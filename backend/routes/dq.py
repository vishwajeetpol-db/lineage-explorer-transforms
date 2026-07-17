"""Data quality rules — capability 23.

Endpoints:
  GET  /api/dq-rules              — list all DQ rules (optionally filtered by table)
  GET  /api/dq-rules/columns      — list column-level DQ rules for a specific table
  POST /api/dq-rules              — add or update a DQ rule
  DELETE /api/dq-rules/{rule_id}  — remove a rule

DQ rules are persisted in an app-owned Delta table `dq_rules` inside the
lineage schema.  They are conceptually separate from Expectations defined
inside SDP pipelines; this table stores app-managed rules you can annotate
columns with regardless of how the table was produced.

Table DDL (auto-created on first write):
    dq_rules (
        rule_id       STRING,
        table_fqn     STRING,
        column_name   STRING,
        rule_type     STRING,   -- NOT_NULL | UNIQUE | RANGE | REGEX | CUSTOM
        expression    STRING,   -- SQL expression checked against the column
        severity      STRING,   -- ERROR | WARN
        created_by    STRING,
        created_at    TIMESTAMP,
        updated_at    TIMESTAMP,
        notes         STRING
    )
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dq-rules", tags=["dq"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
DQ_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_rules"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_FULL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


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


def _ensure_dq_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {DQ_TABLE} ("
            f"  rule_id STRING, table_fqn STRING, column_name STRING, "
            f"  rule_type STRING, expression STRING, severity STRING, "
            f"  created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP, notes STRING"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"dq: could not ensure {DQ_TABLE}: {e}")


class DQRuleIn(BaseModel):
    rule_id: Optional[str] = None
    table_fqn: str
    column_name: Optional[str] = None
    rule_type: str = "CUSTOM"
    expression: str
    severity: str = "ERROR"
    notes: Optional[str] = ""


@router.get("")
async def list_dq_rules(
    request: Request,
    table_fqn: Optional[str] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
):
    """List DQ rules, optionally filtered to a specific table."""
    table_filter = ""
    if table_fqn:
        if not _FULL_NAME_RE.match(table_fqn):
            raise HTTPException(status_code=400, detail="Invalid table_fqn")
        table_filter = f"WHERE table_fqn = '{table_fqn}' "
    try:
        _ensure_dq_table()
        rows = _execute_sql(
            f"SELECT * FROM {DQ_TABLE} {table_filter} "
            f"ORDER BY table_fqn, column_name, rule_type LIMIT {limit}"
        )
        return {"rules": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/columns")
async def list_dq_rules_for_columns(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return per-column DQ expectations for a table."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    full_name = f"{c}.{s}.{t}"
    try:
        _ensure_dq_table()
        rows = _execute_sql(
            f"SELECT column_name, rule_type, expression, severity, notes "
            f"FROM {DQ_TABLE} "
            f"WHERE table_fqn = '{full_name}' "
            f"ORDER BY column_name, rule_type"
        )
        # Group by column
        by_col: dict[str, list] = {}
        for r in rows:
            col = r.get("column_name") or "__table__"
            by_col.setdefault(col, []).append(r)
        return {"table_fqn": full_name, "columns": by_col}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("")
async def upsert_dq_rule(request: Request, rule: DQRuleIn):
    """Add or update a DQ rule. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to modify DQ rules.")
    if not _FULL_NAME_RE.match(rule.table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn")
    safe = lambda s: (s or "").replace("'", "")
    import hashlib
    rule_id = safe(rule.rule_id or hashlib.sha256(
        f"{rule.table_fqn}{rule.column_name}{rule.expression}".encode()
    ).hexdigest()[:12])
    try:
        _ensure_dq_table()
        _execute_sql(
            f"INSERT OVERWRITE {DQ_TABLE} "
            f"SELECT rule_id, table_fqn, column_name, rule_type, expression, severity, "
            f"       created_by, created_at, updated_at, notes "
            f"FROM {DQ_TABLE} WHERE rule_id != '{rule_id}' "
            f"UNION ALL "
            f"SELECT '{rule_id}', '{safe(rule.table_fqn)}', "
            f"'{safe(rule.column_name)}', '{safe(rule.rule_type)}', "
            f"'{safe(rule.expression)}', '{safe(rule.severity)}', "
            f"'{safe(email)}', current_timestamp(), current_timestamp(), '{safe(rule.notes)}'"
        )
        return {"rule_id": rule_id, "status": "upserted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{rule_id}")
async def delete_dq_rule(request: Request, rule_id: str):
    """Delete a DQ rule by ID. Admin-gated."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    safe_id = rule_id.replace("'", "")[:64]
    try:
        _ensure_dq_table()
        _execute_sql(
            f"DELETE FROM {DQ_TABLE} WHERE rule_id = '{safe_id}'"
        )
        return {"rule_id": safe_id, "status": "deleted"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
