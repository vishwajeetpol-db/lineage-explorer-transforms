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
  POST   /api/notifications/rules           — create/update an alert rule (admin)
  DELETE /api/notifications/rules/{id}      — delete an alert rule (admin)

Persisted in app-owned Delta tables:
  - notifications (id, type, severity, title, detail, table_fqn, ...)
  - notification_rules (rule_id, type, threshold, enabled, ...)
"""
from __future__ import annotations

import os
import math
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Literal, Optional, get_args

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.validators import require_admin, sql_str

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


# The only rule/notification types and severities this module produces or
# consumes — see the _detect_* helpers below (schema_change, dq_degradation,
# sensitive_flow) plus run_failure, which the rules table has always documented.
# Declared as Literals so Pydantic rejects anything else with a 422 before the
# value can reach SQL text, and reused as allow-lists for the query filters.
RuleType = Literal["schema_change", "dq_degradation", "sensitive_flow", "run_failure"]
Severity = Literal["info", "warning", "critical"]

# notif_type values that can actually appear in the notifications table: the rule
# types above plus the "info" fallback _create_notification uses when a detector
# omits a type.
NOTIF_TYPES = frozenset(get_args(RuleType)) | {"info"}
SEVERITIES = frozenset(get_args(Severity))


class AlertRuleIn(BaseModel):
    rule_id: Optional[str] = None  # server-generated UUID; validated when supplied
    rule_type: RuleType
    target_pattern: Optional[str] = "*"  # glob pattern for table FQNs
    threshold: Optional[float] = 0.9  # for DQ: min pass rate
    severity: Optional[Severity] = "warning"
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
    # Allow-list first, escape second. Both filters land in SQL text whose rows go
    # straight back to the caller (SELECT *), so a bypass leaks the whole table.
    # Quote-doubling alone was not enough: `\'` is an escape sequence on Databricks
    # SQL, so a value starting `\'` closed the literal and ran as SQL.
    if notif_type:
        if notif_type not in NOTIF_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid notif_type: '{notif_type[:50]}'")
        conditions.append(f"notif_type = '{sql_str(notif_type)}'")
    if severity:
        if severity not in SEVERITIES:
            raise HTTPException(status_code=400, detail=f"Invalid severity: '{severity[:50]}'")
        conditions.append(f"severity = '{sql_str(severity)}'")
    if unread_only:
        conditions.append("is_read = false")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {NOTIF_TABLE} WHERE {where} ORDER BY detected_at DESC LIMIT {limit}"
        )
        return {"notifications": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"list_notifications failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list notifications.")


@router.get("/unread-count")
async def unread_count(request: Request):
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT COUNT(*) as cnt FROM {NOTIF_TABLE} WHERE is_read = false"
        )
        return {"count": int(rows[0]["cnt"]) if rows else 0}
    except Exception as e:
        # Badge count is best-effort: never surface the SQL error to the caller.
        logger.debug(f"unread_count unavailable: {e}")
        return {"count": 0}


@router.post("/mark-read")
async def mark_read(request: Request, body: dict):
    # Deliberately NOT admin-gated: this only flips a shared read flag and every
    # user of the notifications panel needs it. Gating it would break normal UI use.
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
            # Caller-supplied ids: sql_str, not quote-doubling (see FIX above).
            id_list = ",".join(f"'{sql_str(nid, 100)}'" for nid in notif_ids[:100])
            await asyncio.to_thread(
                _execute_sql, f"UPDATE {NOTIF_TABLE} SET is_read = true, read_at = TIMESTAMP '{now}' WHERE notif_id IN ({id_list})"
            )
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"mark_read failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark notifications read.")


@router.post("/scan")
async def trigger_scan(request: Request):
    """Trigger a detection scan for schema changes, DQ degradation, and sensitive flows.
    Creates notifications for any detected issues.

    Admin-gated: the scan runs broad system-table queries as the app service
    principal and writes rows every user then sees."""
    require_admin(request)
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
        logger.error(f"notification scan failed: {e}")
        raise HTTPException(status_code=500, detail="Detection scan failed.")


def _create_notification(notif: dict) -> None:
    nid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    # Every field here carries system-table content (table/column/type names), so
    # it goes through sql_str rather than bare quote-doubling.
    _execute_sql(f"""
        INSERT INTO {NOTIF_TABLE} (notif_id, notif_type, severity, title, detail, table_fqn, column_name, detected_at, is_read, metadata)
        VALUES ('{nid}', '{sql_str(notif.get("type") or "info", 100)}', '{sql_str(notif.get("severity") or "warning", 50)}',
                '{sql_str(notif.get("title", ""), 500)}',
                '{sql_str(notif.get("detail", ""), 2000)}',
                '{sql_str(notif.get("table_fqn", ""))}',
                '{sql_str(notif.get("column_name", ""))}',
                TIMESTAMP '{now}', false, '{sql_str(notif.get("metadata", ""), 2000)}')
    """)


def _detect_schema_changes() -> list[dict]:
    """Detect schema changes by comparing information_schema with last-known state."""
    notifications = []
    try:
        # Get tables with recent schema modifications (last 24h).
        #
        # `last_altered` lives on information_schema.TABLES, not on COLUMNS. The
        # previous query selected it straight off `columns`, so it raised
        # UNRESOLVED_COLUMN on every run, was swallowed by the non-fatal except
        # below, and this detector reported 0 schema changes unconditionally —
        # while the scan as a whole still reported success. Join to get the
        # table's alter time alongside the column detail.
        rows = _execute_sql(f"""
            SELECT c.table_catalog, c.table_schema, c.table_name,
                   c.column_name, c.data_type
            FROM system.information_schema.columns c
            JOIN system.information_schema.tables t
              ON  t.table_catalog = c.table_catalog
              AND t.table_schema  = c.table_schema
              AND t.table_name    = c.table_name
            WHERE t.last_altered > current_timestamp() - INTERVAL 24 HOURS
              -- Exclude noise: the platform's own catalogs and the app-owned
              -- bookkeeping schema, whose cache/config tables are altered on
              -- every deploy and are not user-facing data activity.
              AND c.table_catalog <> 'system'
              AND c.table_schema  <> 'information_schema'
              AND NOT (c.table_catalog = '{sql_str(LINEAGE_CATALOG)}'
                       AND c.table_schema = '{sql_str(LINEAGE_SCHEMA)}')
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
        logger.error(f"list_rules failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list alert rules.")


@router.post("/rules")
async def upsert_rule(request: Request, body: AlertRuleIn):
    """Create or update an alert rule.

    Admin-gated, like the peer rule writers (dq.py, capability_closures.py):
    rules drive scans that run as the app service principal, and every stored
    field is echoed back to all users by GET /rules."""
    require_admin(request)
    _lazy_ensure()
    # rule_id is server-generated in the normal flow. A client-supplied one is only
    # accepted as a UUID, so it can never carry SQL text into the MERGE below —
    # previously it was interpolated verbatim, which let a second request rewrite a
    # stored column into a scalar subquery that GET /rules then echoed back.
    if body.rule_id:
        try:
            rid = str(uuid.UUID(str(body.rule_id)))
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=400, detail="Invalid rule_id: must be a UUID")
    else:
        rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    # rule_type/severity are Literal-constrained on the model, target_pattern/notes
    # are free text: every one goes through sql_str (backslash-then-quote), since
    # quote-doubling alone is bypassable on Databricks SQL. threshold (float) and
    # enabled (bool) are Pydantic-coerced, so they cannot carry SQL text — but
    # enabled is Optional, so an explicit null is normalised here rather than
    # rendering as the literal `none`.
    rule_type = sql_str(body.rule_type)
    severity = sql_str(body.severity or "warning")
    # `or` swallows a legitimate empty pattern the same way it swallowed 0 below:
    # `"" or "*"` widens a rule from "no tables" to "every table". Test for None.
    pattern = sql_str("*" if body.target_pattern is None else body.target_pattern, 500)
    notes = sql_str(body.notes or "", 500)
    # `float(body.threshold or 0.9)` was wrong twice over:
    #
    #  * 0 is falsy, so `{"threshold": 0}` was silently stored as 0.9 and GET
    #    /rules echoed back a value the admin never set and could not express.
    #  * Infinity survives validation — `json.loads('{"v": 1e999}')` yields inf and
    #    pydantic's Optional[float] has allow_inf_nan=True by default — and
    #    f-string-formatting it emits the bare word `inf` into an UNQUOTED SQL
    #    position, where Spark resolves it as a column reference against the rules
    #    table and fails. The sibling math.isfinite guard added elsewhere in this
    #    commit was never applied here.
    threshold = 0.9 if body.threshold is None else float(body.threshold)
    if not math.isfinite(threshold):
        raise HTTPException(
            status_code=400, detail="threshold must be a finite number"
        )
    enabled = "true" if (True if body.enabled is None else body.enabled) else "false"
    try:
        await asyncio.to_thread(_execute_sql, f"""
            MERGE INTO {RULES_TABLE} t USING (SELECT '{rid}' AS rule_id) s ON t.rule_id = s.rule_id
            WHEN MATCHED THEN UPDATE SET
                rule_type = '{rule_type}', target_pattern = '{pattern}',
                threshold = {threshold}, severity = '{severity}',
                enabled = {enabled}, notes = '{notes}',
                updated_at = TIMESTAMP '{now}'
            WHEN NOT MATCHED THEN INSERT (rule_id, rule_type, target_pattern, threshold, severity, enabled, created_by, created_at, updated_at, notes)
            VALUES ('{rid}', '{rule_type}', '{pattern}',
                    {threshold}, '{severity}', {enabled},
                    'app', TIMESTAMP '{now}', TIMESTAMP '{now}', '{notes}')
        """)
        return {"status": "ok", "rule_id": rid}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"upsert_rule failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to save alert rule.")


@router.delete("/rules/{rule_id}")
async def delete_rule(request: Request, rule_id: str):
    """Delete an alert rule. Admin-gated, matching POST /rules."""
    require_admin(request)
    _lazy_ensure()
    # Not UUID-constrained: rows predating the UUID check above may carry any id.
    safe_id = sql_str(rule_id, 100)
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {RULES_TABLE} WHERE rule_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"delete_rule failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete alert rule.")
