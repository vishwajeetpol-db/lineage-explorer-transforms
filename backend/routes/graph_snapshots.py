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

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/snapshots", tags=["snapshots"])

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


class CaptureRequest(BaseModel):
    catalog: str
    schema_name: Optional[str] = None
    label: Optional[str] = ""  # User-friendly label for this snapshot


@router.post("/capture")
async def capture_snapshot(request: Request, body: CaptureRequest):
    """Capture current lineage graph as a point-in-time snapshot."""
    _lazy_ensure()
    scope = f"{body.catalog}.{body.schema_name}" if body.schema_name else body.catalog

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
        label = (body.label or f"Snapshot {now[:10]}").replace("'", "''")[:200]
        graph_escaped = graph_json.replace("'", "''")

        # Cap graph_json at 10MB to prevent oversized rows
        if len(graph_escaped) > 10_000_000:
            raise HTTPException(status_code=413, detail="Graph too large to snapshot (>10MB)")

        _execute_sql(f"""
            INSERT INTO {SNAPSHOTS_TABLE}
            (snapshot_id, scope, label, captured_at, captured_by, node_count, edge_count, graph_json, metadata)
            VALUES ('{sid}', '{scope.replace(chr(39), chr(39)*2)}', '{label}',
                    TIMESTAMP '{now}', 'app', {len(nodes_data)}, {len(edges_data)},
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("")
async def list_snapshots(
    request: Request,
    scope: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """List available snapshots."""
    _lazy_ensure()
    where = f"WHERE scope = '{scope.replace(chr(39), chr(39)*2)}'" if scope else ""
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"SELECT snapshot_id, scope, label, captured_at, captured_by, node_count, edge_count FROM {SNAPSHOTS_TABLE} {where} ORDER BY captured_at DESC LIMIT {limit}"
        )
        return {"snapshots": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{snapshot_id}")
async def get_snapshot(request: Request, snapshot_id: str):
    """Retrieve a specific snapshot with full graph data."""
    _lazy_ensure()
    safe_id = snapshot_id.replace("'", "''")[:100]
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
        return row
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/diff")
async def diff_snapshots(
    request: Request,
    snapshot_a: str = Query(..., description="Older snapshot ID"),
    snapshot_b: str = Query(..., description="Newer snapshot ID"),
):
    """Compare two snapshots and return added/removed nodes and edges."""
    _lazy_ensure()
    safe_a = snapshot_a.replace("'", "''")[:100]
    safe_b = snapshot_b.replace("'", "''")[:100]

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
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{snapshot_id}")
async def delete_snapshot(request: Request, snapshot_id: str):
    _lazy_ensure()
    safe_id = snapshot_id.replace("'", "''")[:100]
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {SNAPSHOTS_TABLE} WHERE snapshot_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
