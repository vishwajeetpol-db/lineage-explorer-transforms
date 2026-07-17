"""Diagnostics routes — capabilities 33-36.

Endpoints:
  POST /api/diagnostics/root-cause          — cap 33: root-cause tracing from anomaly
  GET  /api/diagnostics/scd                 — cap 34: CDC/SCD spec for a table
  GET  /api/diagnostics/scd/targets         — cap 34: list all CDC target tables
  GET  /api/diagnostics/schema-changes      — cap 35: breaking-change detection
  GET  /api/diagnostics/profile             — cap 36: column profiling overlay
"""
from __future__ import annotations

from typing import Optional
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.server.root_cause import trace_root_cause
from backend.server.scd_lineage import get_cdc_spec, list_cdc_targets
from backend.server.schema_change import detect_breaking_changes, detect_schema_changes_for_catalog
from backend.server.column_profiling import get_column_profile

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_COL_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


# ---------------------------------------------------------------------------
# Cap 33 — Root-cause tracing
# ---------------------------------------------------------------------------

class RootCauseIn(BaseModel):
    catalog: str
    schema_name: str
    table: str
    column: str
    anomaly_timestamp: Optional[str] = None
    max_hops: int = 6


@router.post("/root-cause")
async def root_cause(request: Request, body: RootCauseIn):
    """Walk the lineage graph backwards from a failing column to rank root-cause candidates."""
    c = _validate(body.catalog, "catalog")
    s = _validate(body.schema_name, "schema")
    t = _validate(body.table, "table")
    col = _validate(body.column, "column")
    hops = min(max(body.max_hops, 1), 10)
    try:
        return trace_root_cause(
            catalog=c,
            schema=s,
            table=t,
            column=col,
            anomaly_timestamp=body.anomaly_timestamp,
            max_hops=hops,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Cap 34 — SCD/CDC lineage visualization
# ---------------------------------------------------------------------------

@router.get("/scd")
async def scd_spec(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return the captured CDC/SCD spec for a table with first-class graph annotations."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        spec = get_cdc_spec(c, s, t)
        if spec is None:
            return {
                "found": False,
                "detail": f"No captured CDC spec for {c}.{s}.{t}. Use capture_cdc_spec() in your pipeline.",
            }
        return {"found": True, "spec": spec}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/scd/targets")
async def scd_targets(request: Request):
    """List all tables with captured CDC specs."""
    try:
        return {"targets": list_cdc_targets()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Cap 35 — Schema change / breaking-change detection
# ---------------------------------------------------------------------------

@router.get("/schema-changes")
async def schema_changes(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: Optional[str] = Query(None, description="Omit to scan all tables in the schema"),
):
    """Detect upstream schema changes that break transformation expressions."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    try:
        if table:
            t = _validate(table, "table")
            return detect_breaking_changes(c, s, t)
        else:
            results = detect_schema_changes_for_catalog(c, schema=s)
            return {
                "catalog": c,
                "schema": s,
                "tables_with_breaking_changes": len(results),
                "results": results,
            }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Cap 36 — Column profiling overlay
# ---------------------------------------------------------------------------

@router.get("/profile")
async def column_profile(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    columns: Optional[str] = Query(None, description="Comma-separated column names (default: all up to 10)"),
    live: bool = Query(False, description="Run a live ad-hoc profiling query (may be slow)"),
):
    """Return column statistics overlay (null %, distinct count, value stats)."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    col_list: Optional[list[str]] = None
    if columns:
        raw_cols = [col.strip() for col in columns.split(",") if col.strip()]
        # Validate each
        for col in raw_cols:
            if not _COL_RE.match(col):
                raise HTTPException(status_code=400, detail=f"Invalid column name: '{col}'")
        col_list = raw_cols
    if live:
        from backend.main import _get_user_info
        _, is_admin = _get_user_info(request)
        if not is_admin:
            raise HTTPException(status_code=403, detail="Admin required for live profiling (live=true).")
    try:
        return get_column_profile(
            catalog=c,
            schema=s,
            table=t,
            columns=col_list,
            live_profile=live,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Cap 31 — Live billing signal for a Control Panel card
# ---------------------------------------------------------------------------

@router.get("/billing/{flag_id}")
async def capability_billing(request: Request, flag_id: str):
    """Return live billing stats for a capability (Control Panel card enrichment)."""
    safe_id = flag_id.replace("/", "").replace("..", "")[:64]
    try:
        from backend.feature_flags import get_capability_live_billing
        return get_capability_live_billing(safe_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Cap 32 — Federated peer trust verification + sync job trigger
# ---------------------------------------------------------------------------

@router.get("/federated/verify/{peer_alias}")
async def verify_federated_peer(request: Request, peer_alias: str):
    """Live connectivity handshake with a registered federated peer."""
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    safe_alias = peer_alias.replace("'", "")[:64]
    try:
        from backend.federated_sync import verify_peer_trust
        return verify_peer_trust(safe_alias)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/federated/sync/{peer_alias}")
async def trigger_federated_sync(request: Request, peer_alias: str):
    """Trigger the cross-workspace sync Lakeflow Job for a registered peer. Admin-gated."""
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required.")
    safe_alias = peer_alias.replace("'", "")[:64]
    try:
        from backend.federated_sync import trigger_peer_sync_job
        return trigger_peer_sync_job(safe_alias, actor=email or "unknown")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
