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
import json
import logging
from typing import Optional

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.server.entities import resolve_entity, resolve_entities
from backend.server.producer_source import (
    analyze_producer,
    resolve_column_transformations,
    overview_column_transformations,
    list_all_versions,
    compare_transformation_versions,
    compare_producers,
    _columns_for_ref,
)
from backend.server.analysis_store import (
    list_analyses,
    list_versions,
    get_version,
)
from backend.validators import sql_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/lineage", tags=["lineage-ext"])
analyze_router = APIRouter(tags=["lineage-ext"])

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
LINEAGE_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py
_FULL_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}\.[A-Za-z0-9_]{1,255}$")
_ENTITY_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_./@ +-]{1,256}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


def _assert_producer_of(entity_type: str, entity_id: str, target_table: str) -> None:
    """Reject a (producer, target) pair that Unity Catalog never recorded.

    Without this, every endpoint that resolves a producer's source is a confused
    deputy: `entity_id` is free-form (`_ENTITY_ID_RE` permits `/`, `.` and `@`, so
    it accepts any workspace path) and the fetch runs as the APP service
    principal, whose read scope is deliberately broader than any one caller's.
    A caller could therefore aim the fetch at an unrelated notebook or job —
    `/Users/someone-else/private` — and have its source stored, returned, or
    summarised back to them.

    Constraining the pair to producers UC actually recorded for `target_table`
    keeps the feature working for real lineage while removing the
    arbitrary-target primitive. Fails CLOSED: if the lineage lookup can't be
    performed we refuse rather than fall back to trusting the caller.
    """
    et = (entity_type or "").strip().upper()
    eid = (entity_id or "").strip()
    try:
        rows = _execute_sql(
            f"SELECT 1 AS ok FROM system.access.table_lineage "
            f"WHERE target_table_full_name = '{sql_str(target_table)}' "
            f"  AND upper(entity_type) = '{sql_str(et)}' "
            f"  AND entity_id = '{sql_str(eid)}' "
            f"  AND event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp()) "
            f"LIMIT 1"
        )
    except Exception as e:
        logger.warning(f"producer authorization check failed for {et}:{eid} -> {target_table}: {e}")
        raise HTTPException(
            status_code=503,
            detail="Could not verify that this producer writes the target table. "
                   "The app needs SELECT on system.access.table_lineage.",
        )
    if not rows:
        raise HTTPException(
            status_code=403,
            detail=f"{et} {eid} is not a recorded producer of {target_table} "
                   f"within the last {LINEAGE_LOOKBACK_DAYS} days.",
        )


def _strip_source(row: Optional[dict], is_admin: bool) -> Optional[dict]:
    """Remove the stored source snapshot unless the caller is an admin.

    `analysis_store._decode_row` returns `source_code` verbatim, so any endpoint
    returning a full row hands out the producer's code. Only the admin-facing
    diff genuinely needs it; everyone else gets the derived columns.
    """
    if row is None or is_admin:
        return row
    out = dict(row)
    if out.get("source_code") is not None:
        out["source_code"] = None
        out["source_code_redacted"] = True
    return out


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
    model: Optional[str] = None  # LLM serving-endpoint name to use for this run


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
    email, is_admin = _get_user_info(request)
    # The source fetch runs as the app SP, so the caller must not be able to
    # nominate an arbitrary workspace object as the "producer".
    if not is_admin:
        _assert_producer_of(et, eid, body.target_table)
    try:
        return analyze_producer(
            entity_type=et,
            entity_id=eid,
            target_table=body.target_table,
            actor=email or "unknown",
            force_rerun=body.force_rerun,
            target_columns=body.target_columns,
            model=body.model,
        )
    except HTTPException:
        raise
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


class ColumnTransformIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    force_rerun: bool = False
    model: Optional[str] = None


@analyze_router.post("/api/column-transformations")
async def column_transformations(request: Request, body: ColumnTransformIn):
    """Unified column-transformation lineage for a table, resolved best-source-
    first: captured Spark plan → captured CDC spec → stored LLM version → fresh
    LLM. Mirrors the reference tool's precedence."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    et = (body.entity_type or "").strip().upper() or None
    eid = (body.entity_id or "").strip() or None
    if eid and not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if et and eid and not is_admin:
        _assert_producer_of(et, eid, f"{c}.{s}.{t}")
    try:
        return resolve_column_transformations(
            catalog=c, schema=s, table=t,
            entity_type=et, entity_id=eid,
            actor=email or "unknown",
            force_rerun=body.force_rerun,
            model=body.model,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CTOverviewIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    refresh: bool = False
    model: Optional[str] = None


@analyze_router.post("/api/column-transformations/overview")
async def column_transformation_overview(request: Request, body: CTOverviewIn):
    """Plain-English LLM overview of a table's column transformations — an overall
    summary plus a per-column explanation, merged onto each column so the UI can
    draw the source→transform→target graphic. Cached per table (refresh re-runs)."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    et = (body.entity_type or "").strip().upper() or None
    eid = (body.entity_id or "").strip() or None
    if eid and not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if et and eid and not is_admin:
        _assert_producer_of(et, eid, f"{c}.{s}.{t}")
    try:
        return overview_column_transformations(
            catalog=c, schema=s, table=t, entity_type=et, entity_id=eid,
            actor=email or "unknown", refresh=body.refresh, model=body.model,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class LineageExplainIn(BaseModel):
    focus_table: str
    nodes: list[dict] = []
    edges: list[dict] = []
    detail: Optional[str] = "data_and_processing"
    model: Optional[str] = None


@analyze_router.post("/api/lineage/explain")
async def explain_lineage(request: Request, body: LineageExplainIn):
    """Plain-English AI explanation of the CURRENT lineage graph for a business
    audience (used by the Business-view lightbulb). Stateless — the frontend posts
    the on-screen (business-view) nodes + edges so the narrative matches exactly
    what the user sees (datasets-only vs datasets+processing)."""
    from backend.server import llm as llm_client
    if not llm_client.is_llm_configured():
        raise HTTPException(status_code=503, detail="AI explanation is unavailable — no serving endpoint is configured for this app.")
    focus = (body.focus_table or "").strip()
    if not focus:
        raise HTTPException(status_code=400, detail="focus_table is required")
    detail = "data" if (body.detail or "").strip() == "data" else "data_and_processing"
    try:
        return await asyncio.to_thread(
            llm_client.explain_lineage_graph,
            body.nodes or [], body.edges or [], focus, detail, body.model,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CTDeepAnalyzeIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    entity_type: str
    entity_id: str
    model: Optional[str] = None


@analyze_router.post("/api/column-transformations/deep-analyze")
async def column_transformation_deep_analyze(request: Request, body: CTDeepAnalyzeIn):
    """Agentic fallback for metadata-driven frameworks: when normal analysis finds
    no columns, detect the config mechanism, read params, query config tables, and
    derive columns — streaming a step-by-step commentary as newline-delimited JSON."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    et = (body.entity_type or "").strip().upper()
    eid = (body.entity_id or "").strip()
    if not et or not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="entity_type and a valid entity_id are required")
    from backend.main import _get_user_info
    from backend.server.framework_analysis import deep_analyze_stream
    email, is_admin = _get_user_info(request)
    full = f"{c}.{s}.{t}"
    # Reads the producer's source AND queries config tables as the app SP —
    # verify the producer actually writes this table before either happens.
    if not is_admin:
        _assert_producer_of(et, eid, full)

    def gen():
        try:
            for ev in deep_analyze_stream(et, eid, full, actor=email or "unknown", model=body.model):
                yield json.dumps(ev) + "\n"
        except Exception as e:  # never break the stream mid-flight
            yield json.dumps({"type": "error", "message": str(e)}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


class CTVersionsIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None


@analyze_router.post("/api/column-transformations/versions")
async def column_transformation_versions(request: Request, body: CTVersionsIn):
    """Unified version list across sources (captured Spark plans + stored LLM
    analyses) for a table, newest-first. Each entry has a `ref` for compare."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    et = (body.entity_type or "").strip().upper() or None
    eid = (body.entity_id or "").strip() or None
    if eid and not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    return {"versions": list_all_versions(c, s, t, et, eid)}


class CTCompareIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    ref_from: str
    ref_to: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None


_REF_RE = __import__("re").compile(r"^(plan_capture|llm):[0-9]{1,9}$")


@analyze_router.post("/api/column-transformations/compare")
async def column_transformation_compare(request: Request, body: CTCompareIn):
    """Compare two transformation versions from ANY source (captured plan vs LLM,
    or two versions of one source) — per-column diff."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    if not _REF_RE.match(body.ref_from or "") or not _REF_RE.match(body.ref_to or ""):
        raise HTTPException(status_code=400, detail="Invalid version ref (expected 'plan_capture:N' or 'llm:N')")
    et = (body.entity_type or "").strip().upper() or None
    eid = (body.entity_id or "").strip() or None
    if eid and not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    return compare_transformation_versions(c, s, t, body.ref_from, body.ref_to, et, eid)


class ProducerRef(BaseModel):
    entity_type: str
    entity_id: str


class CTCompareProducersIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    producers: list[ProducerRef]
    force_rerun: bool = False


@analyze_router.post("/api/column-transformations/compare-producers")
async def column_transformation_compare_producers(request: Request, body: CTCompareProducersIn):
    """Compare column transformations across MULTIPLE producers of one table —
    per-column matrix flagging where producers compute the same column differently."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    producers = []
    for p in (body.producers or []):
        et = (p.entity_type or "").strip().upper()
        eid = (p.entity_id or "").strip()
        if et and eid:
            if not _ENTITY_ID_RE.match(eid):
                raise HTTPException(status_code=400, detail=f"Invalid entity_id: {eid[:80]}")
            producers.append({"entity_type": et, "entity_id": eid})
    if len(producers) < 2:
        raise HTTPException(status_code=400, detail="Provide at least 2 producers to compare.")
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        for p in producers:
            _assert_producer_of(p["entity_type"], p["entity_id"], f"{c}.{s}.{t}")
    try:
        return await asyncio.to_thread(
            compare_producers, c, s, t, producers, email or "", body.force_rerun,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


class CTVersionIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    ref: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None


@analyze_router.post("/api/column-transformations/version")
async def column_transformation_version(request: Request, body: CTVersionIn):
    """Load ONE transformation version's columns by ref (plan_capture:N | llm:N),
    for viewing a specific version from the history list."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    if not _REF_RE.match(body.ref or ""):
        raise HTTPException(status_code=400, detail="Invalid version ref (expected 'plan_capture:N' or 'llm:N')")
    et = (body.entity_type or "").strip().upper() or None
    eid = (body.entity_id or "").strip() or None
    if eid and not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    v = _columns_for_ref(c, s, t, body.ref, et, eid)
    if v is None:
        raise HTTPException(status_code=404, detail=f"Version {body.ref} not found.")
    return v


@analyze_router.get("/api/analyze-producer/models")
async def analyze_producer_models(request: Request):
    """List LLM serving endpoints available for producer analysis (for the UI dropdown).

    Returns chat/completions-capable Foundation Model endpoints. Falls back to a
    curated default list if the serving inventory can't be read.
    """
    from backend.server.llm import LLM_MODEL_NAME
    # Endpoint name fragments that are NOT chat/completion models (embeddings,
    # rerankers, vision, speech) — the serving `task` field is often empty for
    # FMAPI chat models, so we also filter by name.
    _NON_CHAT = ("bge", "embed", "gte", "rerank", "whisper", "vision", "-tts", "clip")
    models: list[str] = []
    try:
        client = _get_client()
        for ep in client.serving_endpoints.list():
            name = getattr(ep, "name", None)
            if not name:
                continue
            lname = name.lower()
            if any(frag in lname for frag in _NON_CHAT):
                continue
            task = (getattr(ep, "task", None) or "").lower()
            # Skip endpoints that declare a non-chat task; keep unknown-task ones.
            if task and "chat" not in task and "completion" not in task and "llm" not in task:
                continue
            models.append(name)
    except Exception as e:
        logger.info(f"analyze-producer/models: serving inventory unavailable: {e}")

    if not models:
        models = [
            "databricks-claude-sonnet-4-6",
            "databricks-claude-opus-4-8",
            "databricks-gpt-oss-120b",
            "databricks-meta-llama-3-3-70b-instruct",
        ]
    # Ensure the current default is present and first.
    models = sorted(set(models))
    if LLM_MODEL_NAME in models:
        models.remove(LLM_MODEL_NAME)
    models.insert(0, LLM_MODEL_NAME)
    return {"models": models, "default": LLM_MODEL_NAME}


@analyze_router.get("/api/analyze-producer/versions")
async def analyze_producer_versions(
    request: Request,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    target_table: str = Query(...),
):
    """List stored analysis versions (metadata only) for one (producer, target table)."""
    et = (entity_type or "").strip().upper()
    eid = (entity_id or "").strip()
    if not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    if not _FULL_NAME_RE.match(target_table):
        raise HTTPException(status_code=400, detail="Invalid target_table")
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        _assert_producer_of(et, eid, target_table)
    return {"versions": list_versions(et, eid, target_table)}


@analyze_router.get("/api/analyze-producer/version")
async def analyze_producer_version(
    request: Request,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    target_table: str = Query(...),
    version: int = Query(...),
):
    """Return one stored analysis version in full (columns + source snapshot)."""
    et = (entity_type or "").strip().upper()
    eid = (entity_id or "").strip()
    if not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    if not _FULL_NAME_RE.match(target_table):
        raise HTTPException(status_code=400, detail="Invalid target_table")
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        _assert_producer_of(et, eid, target_table)
    v = get_version(et, eid, target_table, version)
    if v is None:
        raise HTTPException(status_code=404, detail=f"No stored analysis version {version}.")
    # The stored snapshot is the producer's raw source — admins only.
    return _strip_source(v, is_admin)


@analyze_router.get("/api/analyze-producer/compare")
async def analyze_producer_compare(
    request: Request,
    entity_type: str = Query(...),
    entity_id: str = Query(...),
    target_table: str = Query(...),
    from_version: int = Query(...),
    to_version: int = Query(...),
):
    """Compare two stored versions — returns both full rows plus a per-column diff
    and whether the source code changed between them."""
    et = (entity_type or "").strip().upper()
    eid = (entity_id or "").strip()
    if not eid or not _ENTITY_ID_RE.match(eid):
        raise HTTPException(status_code=400, detail="Invalid entity_id")
    if not _FULL_NAME_RE.match(target_table):
        raise HTTPException(status_code=400, detail="Invalid target_table")
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        _assert_producer_of(et, eid, target_table)
    a = get_version(et, eid, target_table, from_version)
    b = get_version(et, eid, target_table, to_version)
    if a is None or b is None:
        raise HTTPException(status_code=404, detail="One or both versions not found.")

    def _col_map(row: dict) -> dict:
        out = {}
        for c in row.get("columns", []):
            key = c.get("target_column") or c.get("column")
            if key:
                out[key] = c
        return out

    ma, mb = _col_map(a), _col_map(b)
    all_cols = sorted(set(ma) | set(mb))
    col_diffs = []
    for col in all_cols:
        ca, cb = ma.get(col), mb.get(col)
        if ca is None:
            status = "added"
        elif cb is None:
            status = "removed"
        elif json.dumps(ca, sort_keys=True) != json.dumps(cb, sort_keys=True):
            status = "changed"
        else:
            status = "unchanged"
        col_diffs.append({"column": col, "status": status, "from": ca, "to": cb})

    return {
        # source_changed is derived from the hashes, so the diff stays usable for
        # non-admins without shipping either snapshot's raw source.
        "from": _strip_source(a, is_admin),
        "to": _strip_source(b, is_admin),
        "source_changed": a.get("source_hash") != b.get("source_hash"),
        "column_diffs": col_diffs,
        "changed_count": sum(1 for d in col_diffs if d["status"] != "unchanged"),
    }
