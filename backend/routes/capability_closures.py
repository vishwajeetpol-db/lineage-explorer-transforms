"""Capability gap closures — v2.5.2.

Supplementary endpoints closing remaining scorecard gaps:
  #02 End-to-End Lineage  → BI tool consumer detection + streaming topology
  #07 Versioned Lineage   → Auto-capture scheduling + timeline view
  #11 Data Quality        → DQ trend history + pipeline expectation sync
  #20 Notifications       → Webhook registration + delivery queue

Register this router in main.py: app.include_router(capability_closures.router)

Security fixes applied:
  - A1:  All user inputs validated via _validate() before SQL interpolation
  - A2:  Admin gates on auto-capture and record-metrics
  - A10: bi_consumers returns {available: false} on infra failure instead of silent empty
  - C8:  Streaming topology edge errors logged, not silently swallowed
"""
from __future__ import annotations

import os
import uuid
import json
import math
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.validators import _IDENTIFIER_RE, _FULL_NAME_RE, _validate, require_admin, sql_str
from backend.circuit_breaker import sql_circuit_breaker

logger = logging.getLogger(__name__)
router = APIRouter(tags=["capability-closures"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
DQ_HISTORY_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.dq_metrics_history"
WEBHOOKS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.notification_webhooks"
DELIVERY_QUEUE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.webhook_delivery_queue"
SNAPSHOTS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.graph_snapshots"


def _execute_sql(sql: str) -> list[dict]:
    """Execute SQL with circuit breaker protection (C8 fix)."""
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available.")
    # C8 FIX: Fast-fail if warehouse has been consistently failing
    sql_circuit_breaker.check()
    client = _get_client()
    try:
        resp = client.statement_execution.execute_statement(
            statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
        )
        if resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error.message if resp.status.error else resp.status.state
            sql_circuit_breaker.record_failure()
            raise RuntimeError(f"SQL failed: {err}")
        sql_circuit_breaker.record_success()
        if not resp.result or not resp.result.data_array:
            return []
        columns = [c.name for c in resp.manifest.schema.columns]
        return [dict(zip(columns, row)) for row in resp.result.data_array]
    except RuntimeError:
        raise
    except Exception as e:
        sql_circuit_breaker.record_failure()
        raise RuntimeError(f"SQL failed: {e}")


def _safe_identifier(value: Optional[str]) -> Optional[str]:
    """Validate optional identifier input — returns None if empty, raises 400 if invalid."""
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid identifier: '{v[:50]}'")
    return v


# ===========================================================================
# #02 — BI Tool Consumer Detection + Streaming Topology
# ===========================================================================

@router.get("/api/lineage/bi-consumers")
async def bi_tool_consumers(
    request: Request,
    catalog: Optional[str] = Query(None),
    table: Optional[str] = Query(None),
    days: int = Query(30, ge=1, le=365),
):
    """Detect BI tool consumers (Tableau, PowerBI, Looker, etc.)
    via query history user-agent patterns. Returns tool type + frequency.

    A1 FIX: catalog/table are validated before SQL interpolation.
    A10 FIX: Returns {available: false, error: ...} on infra failure.
    """
    # A1 FIX: Validate inputs before SQL interpolation
    safe_catalog = _safe_identifier(catalog)
    safe_table = _safe_identifier(table)

    filters = []
    if safe_catalog:
        filters.append(f"lower(statement_text) LIKE '%{safe_catalog.lower()}%'")
    if safe_table:
        filters.append(f"lower(statement_text) LIKE '%{safe_table.lower()}%'")
    extra = ("AND " + " AND ".join(filters)) if filters else ""
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT client_application AS bi_tool, COUNT(*) AS query_count,
                   COUNT(DISTINCT executed_by) AS distinct_users, MAX(start_time) AS last_accessed
            FROM system.query.history
            WHERE start_time >= current_timestamp() - INTERVAL {days} DAYS
              AND lower(client_application) RLIKE '(tableau|power.?bi|looker|mode|metabase|sigma|thoughtspot|dbt.?cloud|redash|superset)'
              AND status = 'FINISHED' {extra}
            GROUP BY client_application ORDER BY query_count DESC LIMIT 50
        """)
        return {"bi_consumers": rows, "lookback_days": days}
    except Exception as e:
        # A10 FIX: Signal infrastructure failure instead of silent empty
        logger.warning(f"BI consumers query failed: {e}")
        return {"bi_consumers": [], "available": False, "error": str(e)}


@router.get("/api/lineage/streaming-topology")
async def streaming_topology(request: Request, catalog: Optional[str] = Query(None)):
    """Detect streaming tables + source edges for streaming topology view.

    A1 FIX: catalog validated before SQL interpolation.
    C8 FIX: Edge-fetch errors logged instead of silently swallowed.
    """
    # A1 FIX: Validate catalog
    safe_catalog = _safe_identifier(catalog)
    cat_filter = f"AND t.table_catalog = '{safe_catalog}'" if safe_catalog else ""
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT t.table_catalog, t.table_schema, t.table_name, t.data_source_format, t.last_altered
            FROM system.information_schema.tables t
            WHERE t.table_type = 'STREAMING_TABLE' {cat_filter}
            ORDER BY t.table_catalog, t.table_schema, t.table_name LIMIT 500
        """)
        edges = []
        edge_errors = 0
        for row in rows[:50]:
            fqn = f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}"
            try:
                e = _execute_sql(f"""
                    SELECT DISTINCT source_table_full_name, entity_type
                    FROM system.access.table_lineage
                    WHERE target_table_full_name = '{fqn}' AND event_time > current_timestamp() - INTERVAL 30 DAYS LIMIT 10
                """)
                for r in e:
                    edges.append({"target": fqn, "source": r.get("source_table_full_name", ""), "entity_type": r.get("entity_type", "")})
            except Exception as edge_err:
                # C8 FIX: Log edge-fetch failures instead of silently passing
                edge_errors += 1
                logger.debug(f"Edge fetch failed for {fqn}: {edge_err}")
        result = {"streaming_tables": rows, "streaming_edges": edges, "count": len(rows)}
        if edge_errors:
            result["edge_errors"] = edge_errors
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# #07 — Auto-Capture Scheduling + Timeline
# ===========================================================================

@router.post("/api/snapshots/auto-capture")
async def auto_capture_all_scopes(request: Request):
    """Auto-capture snapshots for all catalogs with recent activity.
    Call on schedule (daily job) to build version history automatically.

    A2 FIX: Admin-gated — expensive scan should not be triggerable by any user.
    """
    # A2 FIX: Require admin for expensive auto-capture operation
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required for auto-capture")
    try:
        catalogs = await asyncio.to_thread(_execute_sql, """
            SELECT DISTINCT target_table_catalog AS catalog
            FROM system.access.table_lineage
            WHERE event_time > current_timestamp() - INTERVAL 24 HOURS LIMIT 20
        """)
        from backend.lineage_service import get_table_lineage
        captured = []
        for row in catalogs:
            cat = row.get("catalog", "")
            if not cat:
                continue
            try:
                lineage = get_table_lineage(cat, None, False)
                nodes = [{"id": n.id, "type": getattr(n, "node_type", "unknown")} for n in lineage.nodes]
                edges_list = [{"source": e.source, "target": e.target} for e in lineage.edges]
                graph_json = json.dumps({"nodes": nodes, "edges": edges_list})
                if len(graph_json) > 10_000_000:
                    continue
                sid = str(uuid.uuid4())
                now = datetime.now(timezone.utc).isoformat()
                _execute_sql(f"""
                    INSERT INTO {SNAPSHOTS_TABLE}
                    (snapshot_id, scope, label, captured_at, captured_by, node_count, edge_count, graph_json, metadata)
                    VALUES ('{sid}', '{cat}', 'Auto {now[:10]}', TIMESTAMP '{now}', 'scheduler',
                            {len(nodes)}, {len(edges_list)}, '{sql_str(graph_json)}', '{{"auto":true}}')
                """)
                captured.append({"catalog": cat, "snapshot_id": sid, "nodes": len(nodes), "edges": len(edges_list)})
            except Exception as e:
                logger.debug(f"Auto-capture failed for {cat}: {e}")
        return {"status": "ok", "captured": captured}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/snapshots/timeline")
async def snapshot_timeline(request: Request, scope: str = Query(...), days: int = Query(30)):
    """Node/edge count timeline for a scope — visualize graph growth.

    A1 FIX: scope validated via _safe_identifier before SQL interpolation.
    """
    # A1 FIX: Validate scope
    safe_scope = _safe_identifier(scope)
    if not safe_scope:
        raise HTTPException(status_code=400, detail="scope is required")
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT snapshot_id, captured_at, node_count, edge_count, label
            FROM {SNAPSHOTS_TABLE}
            WHERE scope = '{safe_scope}'
              AND captured_at >= current_timestamp() - INTERVAL {days} DAYS
            ORDER BY captured_at ASC LIMIT 100
        """)
        return {"scope": safe_scope, "timeline": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# #11 — DQ Trend History + Pipeline Expectation Sync
# ===========================================================================

def _ensure_dq_history():
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {DQ_HISTORY_TABLE} (
            run_id STRING, table_fqn STRING, quality_score DOUBLE,
            rules_evaluated INT, rules_passed INT, rules_failed INT,
            evaluated_at TIMESTAMP, details STRING
        ) USING DELTA""")
    except Exception:
        pass


@router.post("/api/dq-rules/record-metrics")
async def record_dq_metrics(request: Request, body: dict):
    """Store a DQ metrics run for trending. Call after /api/dq-rules/metrics.

    A2 FIX: Admin-gated — writes to Delta should not be ungated.
    """
    # A2 FIX: Require admin for DQ metric recording
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to record DQ metrics")
    _ensure_dq_history()
    run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    raw_fqn = (body.get("table_fqn", "") or "").strip()
    # A1 FIX: Validate table_fqn format (before escaping, so the regex sees the
    # value the caller actually sent)
    if raw_fqn and not _FULL_NAME_RE.match(raw_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn format")
    fqn = sql_str(raw_fqn)
    # A1 FIX: coerce the four numeric columns. They are interpolated at UNQUOTED
    # positions, so without coercion any string in the body — from an admin, but
    # still — is written straight into the statement as SQL.
    try:
        quality_score = float(body.get("quality_score", 0) or 0)
        rules_evaluated = int(body.get("rules_evaluated", 0) or 0)
        rules_passed = int(body.get("rules_passed", 0) or 0)
        rules_failed = int(body.get("rules_failed", 0) or 0)
        if not math.isfinite(quality_score):
            raise ValueError("quality_score must be finite")
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="quality_score must be a number and rules_evaluated/passed/failed integers",
        )
    try:
        await asyncio.to_thread(_execute_sql, f"""
            INSERT INTO {DQ_HISTORY_TABLE} VALUES (
                '{run_id}', '{fqn}', {quality_score},
                {rules_evaluated}, {rules_passed}, {rules_failed},
                TIMESTAMP '{now}', '{sql_str(json.dumps(body.get("details", {})), limit=4000)}')
        """)
        return {"status": "ok", "run_id": run_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/dq-rules/trends")
async def dq_trends(request: Request, table_fqn: str = Query(...), days: int = Query(30)):
    """Quality score trend over time. Returns direction: improving/stable/degrading.

    A1 FIX: table_fqn validated against _FULL_NAME_RE.
    """
    # A1 FIX: Validate table_fqn
    if not _FULL_NAME_RE.match(table_fqn):
        raise HTTPException(status_code=400, detail="Invalid table_fqn format")
    _ensure_dq_history()
    safe_fqn = sql_str(table_fqn)
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT run_id, quality_score, rules_evaluated, rules_passed, rules_failed, evaluated_at
            FROM {DQ_HISTORY_TABLE}
            WHERE table_fqn = '{safe_fqn}'
              AND evaluated_at >= current_timestamp() - INTERVAL {days} DAYS
            ORDER BY evaluated_at ASC LIMIT 200
        """)
        trend = "stable"
        if len(rows) >= 2:
            first = float(rows[0].get("quality_score") or 0)
            last = float(rows[-1].get("quality_score") or 0)
            trend = "degrading" if last < first - 0.05 else "improving" if last > first + 0.05 else "stable"
        return {"table_fqn": table_fqn, "trend": trend, "data_points": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/dq-rules/pipeline-expectations")
async def pipeline_expectations(request: Request, catalog: Optional[str] = Query(None)):
    """List SDP pipeline expectations from streaming/materialized tables.

    A1 FIX: catalog validated before SQL interpolation.
    """
    # A1 FIX: Validate catalog
    safe_catalog = _safe_identifier(catalog)
    cat_filter = f"AND table_catalog = '{safe_catalog}'" if safe_catalog else ""
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT table_catalog, table_schema, table_name, table_type
            FROM system.information_schema.tables
            WHERE table_type IN ('STREAMING_TABLE', 'MATERIALIZED_VIEW') {cat_filter}
            LIMIT 200
        """)
        expectations = []
        for row in rows[:30]:
            try:
                props = _execute_sql(f"""
                    SELECT property_key, property_value FROM system.information_schema.table_properties
                    WHERE table_catalog='{row["table_catalog"]}' AND table_schema='{row["table_schema"]}'
                      AND table_name='{row["table_name"]}' AND lower(property_key) LIKE '%expectation%'
                """)
                if props:
                    expectations.append({"table_fqn": f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}", "expectations": props})
            except Exception:
                pass
        return {"pipeline_tables": len(rows), "tables_with_expectations": expectations}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ===========================================================================
# #20 — Webhook Registration + Delivery Queue
# ===========================================================================

def _ensure_webhook_tables():
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {WEBHOOKS_TABLE} (
            webhook_id STRING, name STRING, url STRING, event_types STRING,
            enabled BOOLEAN, secret STRING, created_by STRING, created_at TIMESTAMP
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {DELIVERY_QUEUE_TABLE} (
            delivery_id STRING, webhook_id STRING, webhook_url STRING,
            payload STRING, status STRING, queued_at TIMESTAMP, delivered_at TIMESTAMP
        ) USING DELTA""")
    except Exception:
        pass


class WebhookIn(BaseModel):
    name: str
    url: str
    event_types: str = "*"  # schema_change,dq_degradation,sensitive_flow,*
    secret: Optional[str] = ""


def _redact_url(url: Optional[str]) -> str:
    """Reduce a webhook URL to scheme://host.

    Slack/Teams-style webhook URLs carry their delivery secret in the PATH
    (`/services/T000/B000/XXXX`), so returning the full URL leaks a credential.
    Scheme+host is enough for an admin to recognise which endpoint a row is.
    """
    if not url:
        return ""
    try:
        parts = urlparse(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        pass
    return "(redacted)"


@router.get("/api/notifications/webhooks")
async def list_webhooks(request: Request):
    """List registered webhook endpoints. Admin-gated.

    A2 FIX: the create/delete peers below both require admin and CHANGELOG.md
    documents all three as admin-gated, but this read was open — and it returned
    the raw `url`, i.e. any user could read every webhook's delivery token. The
    gate is now enforced and `url` is redacted to scheme+host on the way out, so
    a path-embedded secret never leaves the server at all.
    """
    require_admin(request)
    _ensure_webhook_tables()
    try:
        rows = await asyncio.to_thread(_execute_sql,
            f"SELECT webhook_id, name, url, event_types, enabled, created_at FROM {WEBHOOKS_TABLE}")
        for row in rows:
            row["url"] = _redact_url(row.get("url"))
        return {"webhooks": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/notifications/webhooks")
async def register_webhook(request: Request, body: WebhookIn):
    """Register a webhook for push notifications. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    _ensure_webhook_tables()
    wid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    safe = lambda s: sql_str(s, limit=500)
    try:
        await asyncio.to_thread(_execute_sql, f"""
            INSERT INTO {WEBHOOKS_TABLE} VALUES (
                '{wid}', '{safe(body.name)}', '{safe(body.url)}', '{safe(body.event_types)}',
                true, '{safe(body.secret)}', '{safe(email)}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "webhook_id": wid}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/api/notifications/webhooks/{webhook_id}")
async def delete_webhook(request: Request, webhook_id: str):
    """Remove a webhook. Admin-gated."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    try:
        await asyncio.to_thread(_execute_sql,
            f"DELETE FROM {WEBHOOKS_TABLE} WHERE webhook_id = '{sql_str(webhook_id, limit=100)}'")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/notifications/enqueue-delivery")
async def enqueue_delivery(request: Request):
    """Queue unread notifications for webhook delivery. Admin-gated.

    Matches notifications against registered webhooks by event_type,
    creates delivery queue entries. A separate Databricks job polls the
    queue and performs the actual HTTP POST delivery (decoupled for security).

    A2 FIX: this is a write that causes outbound HTTP from the delivery job, and
    it was the only ungated mutation among the webhook endpoints (register and
    delete both require admin) — an anonymous caller could flood every registered
    endpoint with notification traffic.
    """
    require_admin(request)
    _ensure_webhook_tables()
    NOTIF_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.notifications"
    try:
        notifications = await asyncio.to_thread(_execute_sql,
            f"SELECT * FROM {NOTIF_TABLE} WHERE is_read = false ORDER BY detected_at DESC LIMIT 50")
        webhooks = await asyncio.to_thread(_execute_sql,
            f"SELECT * FROM {WEBHOOKS_TABLE} WHERE enabled = true")
        if not notifications or not webhooks:
            return {"status": "ok", "queued": 0}

        queued = 0
        now = datetime.now(timezone.utc).isoformat()
        for wh in webhooks:
            types = (wh.get("event_types", "") or "*").split(",")
            url = wh.get("url", "")
            wh_id = wh.get("webhook_id", "")
            for n in notifications:
                if "*" not in types and n.get("notif_type", "") not in types:
                    continue
                payload = sql_str(json.dumps({
                    "type": n.get("notif_type"), "severity": n.get("severity"),
                    "title": n.get("title"), "detail": n.get("detail"),
                    "table_fqn": n.get("table_fqn"), "detected_at": str(n.get("detected_at", "")),
                }), limit=4000)
                did = str(uuid.uuid4())
                _execute_sql(f"""
                    INSERT INTO {DELIVERY_QUEUE_TABLE} VALUES (
                        '{did}', '{sql_str(wh_id)}', '{sql_str(url)}',
                        '{payload}', 'pending', TIMESTAMP '{now}', NULL)
                """)
                queued += 1

        return {"status": "ok", "queued": queued}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/notifications/delivery-status")
async def delivery_status(request: Request, limit: int = Query(20)):
    """Check webhook delivery queue status (pending/delivered/failed)."""
    _ensure_webhook_tables()
    try:
        rows = await asyncio.to_thread(_execute_sql, f"""
            SELECT delivery_id, webhook_id, status, queued_at, delivered_at
            FROM {DELIVERY_QUEUE_TABLE}
            ORDER BY queued_at DESC LIMIT {limit}
        """)
        pending = sum(1 for r in rows if r.get("status") == "pending")
        return {"deliveries": rows, "pending": pending, "total": len(rows)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
