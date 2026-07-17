"""Extended lineage routes — capabilities 24, 25, 26, 27.

Endpoints:
  GET  /api/lineage/column-path   — cap 24: hop-by-hop column path with per-hop expression
  GET  /api/lineage/entities      — cap 25: resolve entity_type/entity_id to display names
  GET  /api/lineage/freshness     — cap 26: lightweight change-detection fingerprint
  POST /api/analyze-producer      — cap 27: LLM source-code analysis for a producer entity
  GET  /api/analyze-producer/history — cap 28: analysis version history
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.server.entities import resolve_entity, resolve_entities
from backend.server.producer_source import analyze_producer
from backend.server.analysis_store import list_analyses

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lineage", tags=["lineage-ext"])
analyze_router = APIRouter(tags=["lineage-ext"])

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
LINEAGE_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_FULL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")
_ENTITY_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_./@ +-]{1,256}$")


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


# ---------------------------------------------------------------------------
# Capability 24 — Column-path detailed view
# ---------------------------------------------------------------------------

@router.get("/column-path")
async def column_path(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    column: str = Query(...),
    direction: str = Query("upstream", description="upstream | downstream | both"),
    max_hops: int = Query(6, ge=1, le=20),
):
    """Return a hop-by-hop column lineage path with per-hop SQL expression.

    Unlike the flat column_lineage trace in the combined view, each hop
    includes the transformation expression (from transform_edges when
    available, otherwise the raw lineage edge).
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    col = _validate(column, "column")
    direction = direction.lower()
    if direction not in ("upstream", "downstream", "both"):
        raise HTTPException(status_code=400, detail="direction must be upstream, downstream, or both")

    full_name = f"{c}.{s}.{t}"

    def _walk_column_lineage(start_table: str, start_col: str, dir_: str) -> list[dict]:
        """BFS over system.access.column_lineage returning hop-by-hop path."""
        visited: set[tuple] = set()
        path: list[dict] = []
        frontier = [(start_table, start_col, 0)]
        while frontier:
            tbl, col_, hop = frontier.pop(0)
            if (tbl, col_) in visited or hop >= max_hops:
                continue
            visited.add((tbl, col_))
            if dir_ in ("upstream", "both"):
                try:
                    rows = _execute_sql(
                        f"SELECT source_table_full_name, source_column_name, "
                        f"       target_column_transformation "
                        f"FROM system.access.column_lineage "
                        f"WHERE target_table_full_name = '{tbl}' "
                        f"  AND target_column_name = '{col_}' "
                        f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp()) "
                        f"LIMIT 20"
                    )
                    for r in rows:
                        src_tbl = r.get("source_table_full_name", "")
                        src_col = r.get("source_column_name", "")
                        expr = r.get("target_column_transformation", "")
                        path.append({
                            "hop": hop + 1,
                            "direction": "upstream",
                            "source_table": src_tbl,
                            "source_column": src_col,
                            "target_table": tbl,
                            "target_column": col_,
                            "expression": expr or "",
                        })
                        if (src_tbl, src_col) not in visited:
                            frontier.append((src_tbl, src_col, hop + 1))
                except Exception as e:
                    logger.debug(f"column-path: upstream walk error at {tbl}.{col_}: {e}")
            if dir_ in ("downstream", "both"):
                try:
                    rows = _execute_sql(
                        f"SELECT target_table_full_name, target_column_name, "
                        f"       target_column_transformation "
                        f"FROM system.access.column_lineage "
                        f"WHERE source_table_full_name = '{tbl}' "
                        f"  AND source_column_name = '{col_}' "
                        f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp()) "
                        f"LIMIT 20"
                    )
                    for r in rows:
                        tgt_tbl = r.get("target_table_full_name", "")
                        tgt_col = r.get("target_column_name", "")
                        expr = r.get("target_column_transformation", "")
                        path.append({
                            "hop": hop + 1,
                            "direction": "downstream",
                            "source_table": tbl,
                            "source_column": col_,
                            "target_table": tgt_tbl,
                            "target_column": tgt_col,
                            "expression": expr or "",
                        })
                        if (tgt_tbl, tgt_col) not in visited:
                            frontier.append((tgt_tbl, tgt_col, hop + 1))
                except Exception as e:
                    logger.debug(f"column-path: downstream walk error at {tbl}.{col_}: {e}")
        return path

    try:
        path = _walk_column_lineage(full_name, col, direction)
        return {
            "table": full_name,
            "column": col,
            "direction": direction,
            "max_hops": max_hops,
            "path": path,
            "hop_count": max((h["hop"] for h in path), default=0),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Capability 25 — Entity resolution
# ---------------------------------------------------------------------------

class EntityIn(BaseModel):
    entity_type: str
    entity_id: str


@router.get("/entities")
async def resolve_entities_batch(
    request: Request,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
):
    """Resolve a single (entity_type, entity_id) to display name + deep link."""
    et = (entity_type or "").strip().upper()
    eid = (entity_id or "").strip()
    if not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    return resolve_entity(et, eid)


# ---------------------------------------------------------------------------
# Capability 26 — Freshness fingerprint
# ---------------------------------------------------------------------------

@router.get("/freshness")
async def lineage_freshness(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return a lightweight fingerprint for change-detection / auto-refresh.

    The fingerprint is the hash of (max_event_time, edge_count) for the
    table's lineage data. If the fingerprint has changed since the client
    last fetched it, the lineage graph should be re-requested.
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    full_name = f"{c}.{s}.{t}"
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  COUNT(*) AS edge_count, "
            f"  MAX(event_time) AS last_event_at "
            f"FROM system.access.table_lineage "
            f"WHERE (source_table_full_name = '{full_name}' "
            f"       OR target_table_full_name = '{full_name}') "
            f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp())"
        )
        r = rows[0] if rows else {}
        edge_count = int(r.get("edge_count") or 0)
        last_event_at = str(r.get("last_event_at") or "")
        import hashlib
        fingerprint = hashlib.md5(
            f"{edge_count}|{last_event_at}".encode()
        ).hexdigest()[:12]
        return {
            "table_full_name": full_name,
            "fingerprint": fingerprint,
            "edge_count": edge_count,
            "last_event_at": last_event_at,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Capability 27 / 28 — Analyze producer (LLM Approach A)
# ---------------------------------------------------------------------------

class AnalyzeProducerIn(BaseModel):
    entity_type: str
    entity_id: str
    target_table: str
    force_rerun: bool = False
    target_columns: Optional[list[str]] = None


@analyze_router.post("/api/analyze-producer")
async def analyze_producer_endpoint(request: Request, body: AnalyzeProducerIn):
    """Run LLM-based source-code analysis for a producer entity (Approach A).

    Fetches the actual source code of the producer (notebook/query/job/pipeline)
    and calls the configured LLM to infer per-column transformations.
    Results are cached by source hash (cap 28).
    """
    et = (body.entity_type or "").strip().upper()
    eid = (body.entity_id or "").strip()
    if not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    if not _FULL_NAME_RE.match(body.target_table):
        raise HTTPException(status_code=400, detail="Invalid target_table (must be catalog.schema.table)")
    from backend.main import _get_user_info
    email, _ = _get_user_info(request)
    try:
        return analyze_producer(
            entity_type=et,
            entity_id=eid,
            target_table=body.target_table,
            actor=email or "unknown",
            force_rerun=body.force_rerun,
            target_columns=body.target_columns,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@analyze_router.get("/api/analyze-producer/history")
async def analysis_history(
    request: Request,
    entity_type: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Return LLM analysis version history (metadata only). Admin-facing."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    return {"history": list_analyses(entity_type=entity_type, entity_id=entity_id, limit=limit)}
