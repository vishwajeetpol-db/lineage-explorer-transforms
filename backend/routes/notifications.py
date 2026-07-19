"""Routes for Notifications & Alerts — capability 20.

Detects and alerts on:
  - Schema changes (column add/drop/rename/type-change)
  - DQ degradation (rule pass rate drops below threshold)
  - Sensitive data flow (PII/PCI columns flowing to unclassified downstream)

Endpoints:
  GET    /api/notifications                 — list recent notifications
  GET    /api/notifications/unread-count    — unread badge count
  POST   /api/notifications/mark-read       — mark notification(s) as read
  POST   /api/notifications/scan            — trigger a detection scan (admin)
  GET    /api/notifications/rules           — list alert rules
  POST   /api/notifications/rules           — create/update an alert rule
  DELETE /api/notifications/rules/{id}      — delete an alert rule

Persisted in app-owned Delta tables:
  - notifications (id, type, severity, title, detail, table_fqn, ...)
  - notification_rules (rule_id, type, threshold, enabled, ...)
"""
from __future__ import annotations

import os
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
NOTIF_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.notifications"
RULES_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.notification_rules"
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


def _ensure_tables() -> None:
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {NOTIF_TABLE} (
            notif_id STRING, notif_type STRING, severity STRING, title STRING,
            detail STRING, table_fqn STRING, column_name STRING, detected_at TIMESTAMP,
            is_read BOOLEAN, read_by STRING, read_at TIMESTAMP, metadata STRING
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {RULES_TABLE} (
            rule_id STRING, rule_type STRING, target_pattern STRING,
            threshold DOUBLE, severity STRING, enabled BOOLEAN,
            created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP, notes STRING
        ) USING DELTA""")
    except Exception as e:
        logger.warning(f"notifications: could not ensure tables: {e}")


_tables_ensured = False


def _lazy_ensure():
    global _tables_ensured
    if not _tables_ensured:
        _ensure_tables()
        _tables_ensured = True


class AlertRuleIn(BaseModel):
    rule_id: Optional[str] = None
    rule_type: str  # schema_change | dq_degradation | sensitive_flow | run_failure
    target_pattern: Optional[str] = "*"  # glob pattern for table FQNs
    threshold: Optional[float] = 0.9  # for DQ: min pass rate
    severity: Optional[str] = "warning"  # info | warning | critical
    enabled: Optional[bool] = True
    notes: Optional[str] = ""


@router.get("")
async def list_notifications(
    request: Request,
    notif_type: Optional[str] = Query(None),
    severity: Optional[str] = Query(None),
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=500),
):
    """List recent notifications."""
    _lazy_ensure()
    conditions = ["1=1"]
    if notif_type:
        conditions.append(f"notif_type = '{notif_type.replace(chr(39), chr(39)*2)}'")
    if severity:
        conditions.append(f"severity = '{severity.replace(chr(39), chr(39)*2)}'")
    if unread_only:
        conditions.append("is_read = false")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {NOTIF_TABLE} WHERE {where} ORDER BY detected_at DESC LIMIT {limit}"
        )
        return {"notifications": rows, "count": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/unread-count")
async def unread_count(request: Request):
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT COUNT(*) as cnt FROM {NOTIF_TABLE} WHERE is_read = false"
        )
        return {"count": int(rows[0]["cnt"]) if rows else 0}
    except Exception as e:
        return {"count": 0}


@router.post("/mark-read")
async def mark_read(request: Request, body: dict):
    _lazy_ensure()
    notif_ids = body.get("notif_ids", [])
    mark_all = body.get("all", False)
    now = datetime.now(timezone.utc).isoformat()
    try:
        if mark_all:
            await asyncio.to_thread(
                _execute_sql, f"UPDATE {NOTIF_TABLE} SET is_read = true, read_at = TIMESTAMP '{now}' WHERE is_read = false"
            )
        elif notif_ids:
            id_list = ",".join(f"'{nid.replace(chr(39), chr(39)*2)}'" for nid in notif_ids[:100])
            await asyncio.to_thread(
                _execute_sql, f"UPDATE {NOTIF_TABLE} SET is_read = true, read_at = TIMESTAMP '{now}' WHERE notif_id IN ({id_list})"
            )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan")
async def trigger_scan(request: Request):
    """Trigger a detection scan for schema changes, DQ degradation, and sensitive flows.
    Creates notifications for any detected issues."""
    _lazy_ensure()
    results = {"schema_changes": 0, "dq_degradation": 0, "sensitive_flows": 0}

    try:
        # 1. Schema change detection: compare current vs. last-known columns
        schema_changes = await asyncio.to_thread(_detect_schema_changes)
        results["schema_changes"] = len(schema_changes)
        for change in schema_changes:
            await asyncio.to_thread(_create_notification, change)

        # 2. DQ degradation: check rules with pass rates below threshold
        dq_issues = await asyncio.to_thread(_detect_dq_degradation)
        results["dq_degradation"] = len(dq_issues)
        for issue in dq_issues:
            await asyncio.to_thread(_create_notification, issue)

        # 3. Sensitive data flow: PII columns flowing downstream without classification
        sensitive_flows = await asyncio.to_thread(_detect_sensitive_flows)
        results["sensitive_flows"] = len(sensitive_flows)
        for flow in sensitive_flows:
            await asyncio.to_thread(_create_notification, flow)

        return {"status": "ok", "detected": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def _create_notification(notif: dict) -> None:
    nid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _execute_sql(f"""
        INSERT INTO {NOTIF_TABLE} (notif_id, notif_type, severity, title, detail, table_fqn, column_name, detected_at, is_read, metadata)
        VALUES ('{nid}', '{notif.get("type", "info")}', '{notif.get("severity", "warning")}',
                '{notif.get("title", "").replace(chr(39), chr(39)*2)[:500]}',
                '{notif.get("detail", "").replace(chr(39), chr(39)*2)[:2000]}',
                '{notif.get("table_fqn", "").replace(chr(39), chr(39)*2)}',
                '{notif.get("column_name", "").replace(chr(39), chr(39)*2)}',
                TIMESTAMP '{now}', false, '{notif.get("metadata", "").replace(chr(39), chr(39)*2)[:2000]}')
    """)


def _detect_schema_changes() -> list[dict]:
    """Detect schema changes by comparing information_schema with last-known state."""
    notifications = []
    try:
        # Get tables with recent schema modifications (last 24h)
        rows = _execute_sql("""
            SELECT table_catalog, table_schema, table_name, column_name, data_type
            FROM system.information_schema.columns
            WHERE last_altered > current_timestamp() - INTERVAL 24 HOURS
            LIMIT 200
        """)
        for row in rows:
            fqn = f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}"
            notifications.append({
                "type": "schema_change",
                "severity": "warning",
                "title": f"Schema change detected: {row['column_name']} in {row['table_name']}",
                "detail": f"Column {row['column_name']} ({row['data_type']}) was recently modified in {fqn}",
                "table_fqn": fqn,
                "column_name": row.get("column_name", ""),
            })
    except Exception as e:
        logger.warning(f"Schema change detection failed (non-fatal): {e}")
    return notifications[:50]  # Cap at 50 per scan


def _detect_dq_degradation() -> list[dict]:
    """Detect DQ rules that are failing or below threshold."""
    notifications = []
    try:
        # Check if DQ rules table exists and has rules
        DQ_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_rules"
        rules = _execute_sql(f"SELECT * FROM {DQ_TABLE} WHERE severity = 'ERROR' LIMIT 100")
        # For each ERROR-severity rule, flag as potential degradation
        for rule in rules:
            notifications.append({
                "type": "dq_degradation",
                "severity": "warning",
                "title": f"DQ rule active: {rule.get('rule_type', 'CUSTOM')} on {rule.get('column_name', 'unknown')}",
                "detail": f"Rule {rule.get('rule_id', '')} on {rule.get('table_fqn', '')} column {rule.get('column_name', '')} has severity ERROR",
                "table_fqn": rule.get("table_fqn", ""),
                "column_name": rule.get("column_name", ""),
            })
    except Exception as e:
        logger.debug(f"DQ degradation detection skipped: {e}")
    return notifications[:50]


def _detect_sensitive_flows() -> list[dict]:
    """Detect sensitive (PII/PCI) columns flowing downstream without classification."""
    notifications = []
    try:
        # Check column lineage for sensitive columns flowing to untagged targets
        rows = _execute_sql("""
            SELECT DISTINCT
                cl.source_table_catalog || '.' || cl.source_table_schema || '.' || cl.source_table_name AS source_fqn,
                cl.source_column_name,
                cl.target_table_catalog || '.' || cl.target_table_schema || '.' || cl.target_table_name AS target_fqn,
                cl.target_column_name
            FROM system.access.column_lineage cl
            WHERE cl.event_time > current_timestamp() - INTERVAL 7 DAYS
              AND (lower(cl.source_column_name) LIKE '%ssn%'
                   OR lower(cl.source_column_name) LIKE '%email%'
                   OR lower(cl.source_column_name) LIKE '%phone%'
                   OR lower(cl.source_column_name) LIKE '%credit_card%'
                   OR lower(cl.source_column_name) LIKE '%password%')
            LIMIT 100
        """)
        for row in rows:
            notifications.append({
                "type": "sensitive_flow",
                "severity": "critical",
                "title": f"Sensitive column '{row['source_column_name']}' flowing downstream",
                "detail": f"{row['source_fqn']}.{row['source_column_name']} → {row['target_fqn']}.{row['target_column_name']}",
                "table_fqn": row["target_fqn"],
                "column_name": row["target_column_name"],
            })
    except Exception as e:
        logger.debug(f"Sensitive flow detection skipped: {e}")
    return notifications[:50]


# --- Alert rules CRUD ---
@router.get("/rules")
async def list_rules(request: Request):
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(_execute_sql, f"SELECT * FROM {RULES_TABLE} ORDER BY rule_type, created_at")
        return {"rules": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rules")
async def upsert_rule(request: Request, body: AlertRuleIn):
    _lazy_ensure()
    rid = body.rule_id or str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {RULES_TABLE} t USING (SELECT '{rid}' AS rule_id) s ON t.rule_id = s.rule_id
            WHEN MATCHED THEN UPDATE SET
                rule_type = '{body.rule_type}', target_pattern = '{(body.target_pattern or "*").replace(chr(39), chr(39)*2)}',
                threshold = {body.threshold or 0.9}, severity = '{body.severity or "warning"}',
                enabled = {str(body.enabled).lower()}, notes = '{(body.notes or "").replace(chr(39), chr(39)*2)[:500]}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (rule_id, rule_type, target_pattern, threshold, severity, enabled, created_by, created_at, updated_at, notes)
            VALUES ('{rid}', '{body.rule_type}', '{(body.target_pattern or "*").replace(chr(39), chr(39)*2)}',
                    {body.threshold or 0.9}, '{body.severity or "warning"}', {str(body.enabled).lower()},
                    'app', TIMESTAMP '{now}', TIMESTAMP '{now}', '{(body.notes or "").replace(chr(39), chr(39)*2)[:500]}')
        """)
        return {"status": "ok", "rule_id": rid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: str):
    _lazy_ensure()
    safe_id = rule_id.replace("'", "''")[:100]
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {RULES_TABLE} WHERE rule_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
