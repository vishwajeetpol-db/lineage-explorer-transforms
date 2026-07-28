"""Routes for Access & security lineage — capability 20.

Endpoints:
  GET /api/access          — declared grants + empirical audit access + dormant grants
  GET /api/access/schema   — schema-level empirical access summary
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from backend.server.access import get_access_summary, get_schema_access_summary
from backend.server.capability_cache import serve_or_compute

router = APIRouter(prefix="/api/access", tags=["access"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


@router.get("")
async def table_access(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    refresh: bool = Query(False),
):
    """Return declared grants, empirical audit access, and dormant-grant analysis.

    Served from the per-table capability cache unless `refresh=true`.
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    fqn = f"{c}.{s}.{t}"
    from backend.main import _get_user_info
    email, _ = _get_user_info(request)
    try:
        return await asyncio.to_thread(
            serve_or_compute, fqn, "access",
            lambda: get_access_summary(c, s, t), email or "", refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema")
async def schema_access(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
):
    """Return empirical top-table access summary for an entire schema."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    try:
        return {"tables": get_schema_access_summary(c, s)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
