"""Routes for Observability (run health) — capability 19.

Endpoints:
  GET /api/observability            — health for a single entity (job or pipeline)
  GET /api/observability/producers  — health for all producer entities of a table
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional

from backend.server.observability import (
    get_entity_health,
    get_table_producer_health,
)

router = APIRouter(prefix="/api/observability", tags=["observability"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py
_ENTITY_ID_RE = __import__("re").compile(r"^[A-Za-z0-9_./@ +-]{1,256}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


def _validate_entity_id(entity_id: str) -> str:
    v = (entity_id or "").strip()
    if not v or not _ENTITY_ID_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid entity_id: '{v[:80]}'")
    return v


@router.get("")
async def entity_health(
    request: Request,
    entity_type: str = Query(..., description="JOB or PIPELINE"),
    entity_id: str = Query(...),
):
    """Return run/update health stats for a job or pipeline entity."""
    et = (entity_type or "").strip().upper()
    if et not in ("JOB", "PIPELINE"):
        raise HTTPException(status_code=400, detail="entity_type must be JOB or PIPELINE")
    eid = _validate_entity_id(entity_id)
    try:
        return get_entity_health(et, eid)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/producers")
async def table_producer_health(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return health stats for every producer of `catalog.schema.table`."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return {"producers": get_table_producer_health(c, s, t)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
