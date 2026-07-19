"""Routes for Search & discovery — capability 22.

Endpoints:
  GET /api/search             — full-text UC table search
  GET /api/discover/sensitive — tables with PII/PCI columns
  GET /api/discover/orphans   — tables with no lineage events
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from typing import Optional

from backend.server.discovery import search_assets, find_sensitive_tables, find_orphan_tables

router = APIRouter(tags=["discovery"])


@router.get("/api/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=1, max_length=256),
    catalog: Optional[str] = Query(None),
    asset_type: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
):
    """Full-text search across UC table names and comments."""
    catalogs = [catalog] if catalog else None
    types = [asset_type] if asset_type else None
    try:
        results = search_assets(q, catalogs=catalogs, asset_types=types, limit=limit)
        return {"results": results, "count": len(results)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/discover/sensitive")
async def discover_sensitive(
    request: Request,
    catalog: Optional[str] = Query(None),
    schema: Optional[str] = Query(None),
):
    """Return tables that contain at least one PII/PCI column."""
    _IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py
    if catalog and not _IDENTIFIER_RE.match(catalog):
        raise HTTPException(status_code=400, detail="Invalid catalog")
    if schema and not _IDENTIFIER_RE.match(schema):
        raise HTTPException(status_code=400, detail="Invalid schema")
    try:
        tables = find_sensitive_tables(catalog=catalog, schema=schema)
        return {"tables": tables, "count": len(tables)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/discover/orphans")
async def discover_orphans(
    request: Request,
    catalog: Optional[str] = Query(None),
    schema: Optional[str] = Query(None),
):
    """Return tables that have no lineage events (orphaned / stale)."""
    _IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py
    if catalog and not _IDENTIFIER_RE.match(catalog):
        raise HTTPException(status_code=400, detail="Invalid catalog")
    if schema and not _IDENTIFIER_RE.match(schema):
        raise HTTPException(status_code=400, detail="Invalid schema")
    try:
        tables = find_orphan_tables(catalog=catalog, schema=schema)
        return {"tables": tables, "count": len(tables)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
