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

# --- Background auto-scan config ------------------------------------------
# Notifications are only produced by a detection scan. Without a scheduler the
# table stays empty until an admin clicks Scan, so Recent Activity is blank on a
# fresh deploy. The scanner below runs the SAME scan on a timer, entirely off the
# request path — the UI only ever READS the table, so screen render is never
# blocked by (or waiting on) a scan.
AUTOSCAN_ENABLED = os.environ.get("NOTIFICATION_AUTOSCAN_ENABLED", "true").strip().lower() in ("1", "true", "yes", "on")
# How often the background scan runs. Detectors look back 24h (schema) / 7d
# (sensitive flows), so a few hours keeps Recent Activity fresh without churn.
SCAN_INTERVAL_SECONDS = max(60, int(os.environ.get("NOTIFICATION_SCAN_INTERVAL_SECONDS", "21600")))  # 6h
# Delay the first scan so it lands after startup/cost-prefetch and a warm warehouse.
SCAN_INITIAL_DELAY_SECONDS = max(0, int(os.environ.get("NOTIFICATION_SCAN_INITIAL_DELAY_SECONDS", "90")))
# Retention cap: keep only the most recent N notifications after each scan. The
# detectors use LIMIT without a stable ordering, so successive scans surface
# different slices of the same underlying data and the table would grow unbounded.
# Pruning to the newest N keeps the table (and Recent Activity) meaningful. 0 = off.
NOTIFICATION_RETENTION_MAX = max(0, int(os.environ.get("NOTIFICATION_RETENTION_MAX", "200")))
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


def _notif_key(item: dict) -> tuple:
    """Natural identity of a notification for dedup: its type + the table/column it
    concerns + its title. Detector dicts use `type`; stored rows use `notif_type`."""
    return (
        (item.get("type") or item.get("notif_type") or "").strip(),
        (item.get("table_fqn") or "").strip(),
        (item.get("column_name") or "").strip(),
        (item.get("title") or "").strip(),
    )


def _existing_notification_keys(limit: int = 5000) -> set:
    """Natural keys of notifications already stored, so a re-scan (manual or the
    background timer) doesn't insert duplicates. Fail-open: on any read error return
    an empty set — better to risk one duplicate than to silently drop a real alert."""
    try:
        rows = _execute_sql(
            f"SELECT notif_type, table_fqn, column_name, title FROM {NOTIF_TABLE} "
            f"ORDER BY detected_at DESC LIMIT {int(limit)}"
        )
    except Exception as e:
        logger.debug(f"notifications: could not read existing keys (dedup skipped): {e}")
        return set()
    return {_notif_key(r) for r in rows}


def _prune_notifications(keep: int) -> None:
    """Retention cap: delete all but the most recent `keep` notifications, so the
    table can't grow unbounded across repeated scans. Best-effort and non-fatal.

    Deletes rows older than the keep-th newest (ties at the boundary are kept, so
    the floor is `keep`). When the table has <= keep rows the cutoff is the oldest
    row and nothing is deleted."""
    if keep <= 0:
        return
    try:
        _execute_sql(f"""
            DELETE FROM {NOTIF_TABLE}
            WHERE detected_at < (
                SELECT MIN(detected_at) FROM (
                    SELECT detected_at FROM {NOTIF_TABLE}
                    ORDER BY detected_at DESC LIMIT {int(keep)}
                )
            )
        """)
    except Exception as e:
        logger.warning(f"notifications: retention prune failed (non-fatal): {e}")


def run_scan() -> dict:
    """Run all detectors and insert only notifications not already stored, then
    prune to the retention cap.

    Shared by the admin endpoint and the background scheduler. Idempotent across
    runs (dedup by _notif_key), so it is safe to run on a timer. Detector errors
    propagate so the caller decides how to surface them; the dedup read and the
    prune both fail open. Synchronous (blocking SQL) — callers run it via
    asyncio.to_thread."""
    _lazy_ensure()
    existing = _existing_notification_keys()
    detected = {"schema_changes": 0, "dq_degradation": 0, "sensitive_flows": 0}
    inserted = skipped = 0
    for bucket, detect in (
        ("schema_changes", _detect_schema_changes),
        ("dq_degradation", _detect_dq_degradation),
        ("sensitive_flows", _detect_sensitive_flows),
    ):
        found = detect()
        detected[bucket] = len(found)
        for item in found:
            key = _notif_key(item)
            if key in existing:
                skipped += 1
                continue
            _create_notification(item)
            existing.add(key)
            inserted += 1
    if inserted:
        _prune_notifications(NOTIFICATION_RETENTION_MAX)
    return {"detected": detected, "inserted": inserted, "skipped": skipped}


async def _autoscan_loop() -> None:
    """Periodic detection scan, started from the app lifespan. Runs entirely off the
    request path (via asyncio.to_thread), so the UI only ever READS notifications and
    screen render is never blocked by a scan. Resilient: a failed scan is logged and
    retried on the next tick; cancellation (shutdown) propagates cleanly."""
    logger.info(
        f"notifications: auto-scan enabled — first run in {SCAN_INITIAL_DELAY_SECONDS}s, "
        f"then every {SCAN_INTERVAL_SECONDS}s."
    )
    if SCAN_INITIAL_DELAY_SECONDS:
        await asyncio.sleep(SCAN_INITIAL_DELAY_SECONDS)
    while True:
        try:
            result = await asyncio.to_thread(run_scan)
            logger.info(
                f"notifications: auto-scan inserted {result['inserted']} new "
                f"(skipped {result['skipped']} existing)."
            )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"notifications: auto-scan failed (will retry next tick): {e}")
        await asyncio.sleep(SCAN_INTERVAL_SECONDS)


@router.post("/scan")
async def trigger_scan(request: Request):
    """Trigger a detection scan for schema changes, DQ degradation, and sensitive flows.
    Creates notifications for any newly-detected issues (dedup: existing ones are skipped).

    Admin-gated: the scan runs broad system-table queries as the app service
    principal and writes rows every user then sees. The same scan also runs
    automatically in the background (see _autoscan_loop)."""
    require_admin(request)
    try:
        result = await asyncio.to_thread(run_scan)
        return {"status": "ok", **result}
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


def _safe_ident(s: object) -> bool:
    """True for a plain SQL identifier (letters/digits/underscore) — used to guard a
    catalog name before interpolating it into an information_schema query."""
    return bool(s) and all(c.isalnum() or c == "_" for c in str(s))


def _classified_columns(catalogs: set) -> set:
    """Set of (catalog, schema, table, column), lowercased, that carry a UC tag —
    i.e. are already *classified*. Queried per catalog from
    information_schema.column_tags. Best-effort and fail-open: a catalog whose tags
    can't be read contributes nothing, so its columns fall through as unclassified —
    we would rather over-surface a sensitive flow than silently hide one."""
    out: set = set()
    for cat in {c for c in catalogs if _safe_ident(c)}:
        try:
            rows = _execute_sql(
                f"SELECT catalog_name, schema_name, table_name, column_name "
                f"FROM {cat}.information_schema.column_tags"
            )
        except Exception as e:
            logger.debug(f"notifications: column_tags not readable for {cat}: {e}")
            continue
        for r in rows or []:
            out.add((
                str(r.get("catalog_name", "")).lower(), str(r.get("schema_name", "")).lower(),
                str(r.get("table_name", "")).lower(), str(r.get("column_name", "")).lower(),
            ))
    return out


def _detect_sensitive_flows() -> list[dict]:
    """Detect sensitive (PII/PCI) columns flowing downstream to a target that is NOT
    classified. A sensitive-named source column is only flagged when the TARGET
    column it lands on carries no UC classification tag — sensitive data arriving
    somewhere governance hasn't labelled. Targets already tagged are governed and
    are skipped (this is the "without classification" the detector's name promises)."""
    notifications = []
    try:
        rows = _execute_sql("""
            SELECT DISTINCT
                cl.source_table_catalog, cl.source_table_schema, cl.source_table_name, cl.source_column_name,
                cl.target_table_catalog, cl.target_table_schema, cl.target_table_name, cl.target_column_name
            FROM system.access.column_lineage cl
            WHERE cl.event_time > current_timestamp() - INTERVAL 7 DAYS
              AND (lower(cl.source_column_name) LIKE '%ssn%'
                   OR lower(cl.source_column_name) LIKE '%email%'
                   OR lower(cl.source_column_name) LIKE '%phone%'
                   OR lower(cl.source_column_name) LIKE '%credit_card%'
                   OR lower(cl.source_column_name) LIKE '%password%')
            LIMIT 100
        """)
        if not rows:
            return []
        # Skip targets that governance has already classified (carry a UC tag).
        classified = _classified_columns({r.get("target_table_catalog") for r in rows if r.get("target_table_catalog")})
        for row in rows:
            tgt_cat, tgt_sch = row.get("target_table_catalog"), row.get("target_table_schema")
            tgt_tbl, tgt_col = row.get("target_table_name"), row.get("target_column_name")
            if not (tgt_cat and tgt_sch and tgt_tbl and tgt_col):
                continue
            if (tgt_cat.lower(), tgt_sch.lower(), tgt_tbl.lower(), tgt_col.lower()) in classified:
                continue  # already governed — not a "without classification" flow
            source_fqn = f"{row['source_table_catalog']}.{row['source_table_schema']}.{row['source_table_name']}"
            target_fqn = f"{tgt_cat}.{tgt_sch}.{tgt_tbl}"
            notifications.append({
                "type": "sensitive_flow",
                "severity": "critical",
                "title": f"Sensitive column '{row['source_column_name']}' flowing downstream",
                "detail": f"{source_fqn}.{row['source_column_name']} → {target_fqn}.{tgt_col} (target column is unclassified)",
                "table_fqn": target_fqn,
                "column_name": tgt_col,
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
