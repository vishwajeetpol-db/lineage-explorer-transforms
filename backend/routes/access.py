"""Routes for Access & security lineage — capability 20.

Endpoints:
  GET /api/access          — declared grants + empirical audit access + dormant grants
  GET /api/access/schema   — schema-level empirical access summary
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from backend.server.access import get_access_summary, get_schema_access_summary

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
):
    """Return declared grants, empirical audit access, and dormant-grant analysis."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return get_access_summary(c, s, t)
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
