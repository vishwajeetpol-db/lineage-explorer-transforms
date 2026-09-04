"""Routes for Versioned Graph Snapshots — capability 07.

Captures point-in-time snapshots of the lineage graph topology,
allowing historical comparison ("what changed between two dates?").

Endpoints:
  POST /api/snapshots/capture     — capture current graph state as a snapshot
  GET  /api/snapshots             — list available snapshots
  GET  /api/snapshots/{id}        — retrieve a specific snapshot
  GET  /api/snapshots/diff        — diff two snapshots (added/removed nodes+edges)
  DELETE /api/snapshots/{id}      — delete a snapshot

Persisted in app-owned Delta table:
  - graph_snapshots (snapshot_id, scope, captured_at, node_count, edge_count, graph_json)
"""
from __future__ import annotations

import os
import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client, get_table_lineage
from backend.validators import _IDENTIFIER_RE, require_admin, sql_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])


def _is_admin(request: Request) -> bool:
    """Non-raising admin probe, for deciding whether to project attribution
    columns. Use require_admin() when the whole endpoint should be gated."""
    from backend.main import _get_user_info
    try:
        return bool(_get_user_info(request)[1])
    except Exception:  # never let an attribution decision break a read
        return False

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
SNAPSHOTS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.graph_snapshots"
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


def _ensure_table() -> None:
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {SNAPSHOTS_TABLE} (
            snapshot_id STRING, scope STRING, label STRING, captured_at TIMESTAMP,
            captured_by STRING, node_count INT, edge_count INT, graph_json STRING,
            metadata STRING
        ) USING DELTA""")
    except Exception as e:
        logger.warning(f"graph_snapshots: could not ensure table: {e}")


_table_ensured = False


def _lazy_ensure():
    global _table_ensured
    if not _table_ensured:
        _ensure_table()
        _table_ensured = True


def _validate_scope(scope: str) -> str:
    """Constrain a snapshot scope to `catalog` or `catalog.schema`, else HTTP 400.

    Scope is never free text: capture_snapshot below derives it from the request's
    catalog/schema_name, and auto-capture (capability_closures) uses a bare
    catalog name. Allow-listing the shape rejects injection payloads outright
    instead of relying on escaping alone, and keeps the values written by capture
    in the same language the list filter accepts.
    """
    parts = (scope or "").strip().split(".")
    if len(parts) > 2 or not all(_IDENTIFIER_RE.match(p or "") for p in parts):
        raise HTTPException(status_code=400, detail=f"Invalid scope: '{(scope or '')[:50]}'")
    return ".".join(parts)


class CaptureRequest(BaseModel):
    catalog: str
    schema_name: Optional[str] = None
    label: Optional[str] = ""  # User-friendly label for this snapshot


@router.post("/capture")
async def capture_snapshot(request: Request, body: CaptureRequest):
    """Capture current lineage graph as a point-in-time snapshot.

    Deliberately NOT admin-gated: capture is wired into the Export & Interop
    panel for every App user (frontend/src/components/ExportPanel.tsx). Instead
    of gating it, each row records the real caller in captured_by so an
    unexpected snapshot is attributable.
    """
    scope = _validate_scope(
        f"{body.catalog}.{body.schema_name}" if body.schema_name else body.catalog
    )
    _lazy_ensure()
    from backend.main import _get_user_info
    caller, _ = _get_user_info(request)

    try:
        # Fetch current lineage graph
        lineage = await asyncio.to_thread(get_table_lineage, body.catalog, body.schema_name, False)

        # Serialize graph to JSON
        nodes_data = []
        for node in lineage.nodes:
            node_dict = {"id": node.id, "type": getattr(node, "node_type", "unknown")}
            if hasattr(node, "display_name"):
                node_dict["name"] = node.display_name
            if hasattr(node, "entity_type"):
                node_dict["entity_type"] = node.entity_type
            nodes_data.append(node_dict)

        edges_data = []
        for edge in lineage.edges:
            edges_data.append({"source": edge.source, "target": edge.target})

        graph_json = json.dumps({"nodes": nodes_data, "edges": edges_data})

        # Store snapshot
        sid = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        label = sql_str(body.label or f"Snapshot {now[:10]}", 200)
        # No limit: truncating serialized JSON would corrupt it — the 10MB guard
        # below rejects oversized graphs instead.
        graph_escaped = sql_str(graph_json)

        # Cap graph_json at 10MB to prevent oversized rows
        if len(graph_escaped) > 10_000_000:
            raise HTTPException(status_code=413, detail="Graph too large to snapshot (>10MB)")

        _execute_sql(f"""
            INSERT INTO {SNAPSHOTS_TABLE}
            (snapshot_id, scope, label, captured_at, captured_by, node_count, edge_count, graph_json, metadata)
            VALUES ('{sid}', '{sql_str(scope)}', '{label}',
                    TIMESTAMP '{now}', '{sql_str(caller or "unknown", 200)}',
                    {len(nodes_data)}, {len(edges_data)},
                    '{graph_escaped}', '')
        """)

        return {
            "status": "ok",
            "snapshot_id": sid,
            "scope": scope,
            "node_count": len(nodes_data),
            "edge_count": len(edges_data),
        }
    except HTTPException:
        raise
    except Exception as e:
        # Detail stays server-side: the raw text is SQL/SDK error output.
        logger.error(f"snapshot capture failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to capture snapshot")


@router.get("")
async def list_snapshots(
    request: Request,
    scope: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List available snapshots.

    `captured_by` is projected for admins only. This endpoint is deliberately
    ungated so any user can pick versions to compare, but captured_by stopped
    being the constant 'app' when capture started recording the real caller — so
    projecting it unconditionally turned an open endpoint into a directory of the
    workspace email of everyone who has ever captured a snapshot. Recording the
    caller is right; handing it to every reader is not.
    """
    # Validate before _lazy_ensure so a bad filter costs no warehouse round-trip.
    where = f"WHERE scope = '{sql_str(_validate_scope(scope))}'" if scope else ""
    _lazy_ensure()
    attribution = "captured_by, " if _is_admin(request) else ""
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"SELECT snapshot_id, scope, label, captured_at, {attribution}node_count, edge_count FROM {SNAPSHOTS_TABLE} {where} ORDER BY captured_at DESC LIMIT {limit}"
        )
        return {"snapshots": rows}
    except Exception as e:
        logger.error(f"snapshot list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list snapshots")


# ROUTE ORDER IS LOAD-BEARING: Starlette matches in registration order, so
# `/diff` MUST be declared before `/{snapshot_id}`. With the parameterised route
# first, GET /api/snapshots/diff bound snapshot_id="diff" and ran get_snapshot,
# which returned 200 with `{"graph": {"nodes": [], "edges": []}}` — the shape of a
# snapshot, not a diff. Callers could not tell that apart from "nothing changed",
# so the whole diff feature read as permanently empty rather than as broken.
@router.get("/diff")
async def diff_snapshots(
    request: Request,
    snapshot_a: str = Query(..., description="Older snapshot ID"),
    snapshot_b: str = Query(..., description="Newer snapshot ID"),
):
    """Compare two snapshots and return added/removed nodes and edges."""
    _lazy_ensure()
    safe_a = sql_str(snapshot_a, 100)
    safe_b = sql_str(snapshot_b, 100)

    try:
        rows_a = await asyncio.to_thread(
            _execute_sql, f"SELECT graph_json FROM {SNAPSHOTS_TABLE} WHERE snapshot_id = '{safe_a}'"
        )
        rows_b = await asyncio.to_thread(
            _execute_sql, f"SELECT graph_json FROM {SNAPSHOTS_TABLE} WHERE snapshot_id = '{safe_b}'"
        )
        if not rows_a or not rows_b:
            raise HTTPException(status_code=404, detail="One or both snapshots not found")

        graph_a = json.loads(rows_a[0].get("graph_json", "{}"))
        graph_b = json.loads(rows_b[0].get("graph_json", "{}"))

        nodes_a = {n["id"] for n in graph_a.get("nodes", [])}
        nodes_b = {n["id"] for n in graph_b.get("nodes", [])}
        edges_a = {(e["source"], e["target"]) for e in graph_a.get("edges", [])}
        edges_b = {(e["source"], e["target"]) for e in graph_b.get("edges", [])}

        return {
            "nodes_added": list(nodes_b - nodes_a),
            "nodes_removed": list(nodes_a - nodes_b),
            "edges_added": [{"source": s, "target": t} for s, t in (edges_b - edges_a)],
            "edges_removed": [{"source": s, "target": t} for s, t in (edges_a - edges_b)],
            "summary": {
                "nodes_added_count": len(nodes_b - nodes_a),
                "nodes_removed_count": len(nodes_a - nodes_b),
                "edges_added_count": len(edges_b - edges_a),
                "edges_removed_count": len(edges_a - edges_b),
            },
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"snapshot diff failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to diff snapshots")


# Declared AFTER /diff — see the route-order note above.
@router.get("/{snapshot_id}")
async def get_snapshot(request: Request, snapshot_id: str):
    """Retrieve a specific snapshot with full graph data."""
    _lazy_ensure()
    safe_id = sql_str(snapshot_id, 100)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {SNAPSHOTS_TABLE} WHERE snapshot_id = '{safe_id}'"
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Snapshot not found")
        row = rows[0]
        # Parse graph_json back to structured data
        try:
            row["graph"] = json.loads(row.get("graph_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            row["graph"] = {"nodes": [], "edges": []}
        del row["graph_json"]  # Don't send raw JSON string back
        # captured_by is a real workspace email — admins only (see list_snapshots).
        if not _is_admin(request):
            row.pop("captured_by", None)
        return row
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"snapshot fetch failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch snapshot")


@router.delete("/{snapshot_id}")
async def delete_snapshot(request: Request, snapshot_id: str):
    """Delete a snapshot.

    Admin-gated: this is a hard DELETE with no per-user scoping (captured_by is
    not a filter here), so any caller could otherwise walk GET /api/snapshots and
    destroy the whole lineage version history. Matches the gate on the sibling
    write to this table, POST /api/snapshots/auto-capture.
    """
    require_admin(request)
    _lazy_ensure()
    safe_id = sql_str(snapshot_id, 100)
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {SNAPSHOTS_TABLE} WHERE snapshot_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"snapshot delete failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete snapshot")
