"""Routes for Root Cause Analysis — capability 09.

Guided workflow: anomaly/DQ failure on a target column → walk upstream
lineage → correlate with failed producer runs → identify likely source.

Endpoints:
  POST /api/root-cause/analyze        — full root-cause analysis for a column
  GET  /api/root-cause/upstream-path   — upstream column path with health signals
  GET  /api/root-cause/run-failures    — recent run failures near an anomaly window
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.server.root_cause import (
    trace_root_cause,
    trace_root_cause_table,
    _walk_upstream_columns,
    _get_failed_runs_around,
)

router = APIRouter(prefix="/api/root-cause", tags=["root-cause"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


class RootCauseRequest(BaseModel):
    catalog: str
    schema_name: str
    table: str
    column: str
    anomaly_timestamp: Optional[str] = None  # ISO 8601
    max_hops: int = 6


@router.post("/analyze")
async def analyze_root_cause(request: Request, body: RootCauseRequest):
    """Full root-cause analysis: traces upstream column path, correlates with
    producer run failures and DQ violations to produce a ranked list of
    likely root-cause candidates.

    Returns:
      candidates: ranked list of {table, column, hop, score, evidence[]}
      upstream_path: full column lineage path traversed
    """
    catalog = _validate(body.catalog, "catalog")
    schema = _validate(body.schema_name, "schema")
    table = _validate(body.table, "table")
    column = _validate(body.column, "column")

    anomaly_ts_str = None
    if body.anomaly_timestamp:
        try:
            dt = datetime.fromisoformat(body.anomaly_timestamp.replace("Z", "+00:00"))
            anomaly_ts_str = dt.isoformat()
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid anomaly_timestamp (use ISO 8601)")

    try:
        result = await asyncio.to_thread(
            trace_root_cause,
            catalog, schema, table, column,
            anomaly_timestamp=anomaly_ts_str,
            max_hops=body.max_hops,
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trace")
async def root_cause_trace(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    max_hops: int = Query(6),
):
    """Health-based root-cause trace for a table (auto-run, no column needed).

    Walks upstream tables, classifies each table's producers by recent run
    health, and returns a prime suspect + failure path + flagged producers.
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return await asyncio.to_thread(trace_root_cause_table, c, s, t, max_hops)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/upstream-path")
async def upstream_path(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    column: str = Query(...),
    max_hops: int = Query(6),
):
    """Return the upstream column lineage path with producing entities
    and health signals at each hop."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    col = _validate(column, "column")
    try:
        full_name = f"{c}.{s}.{t}"
        path = await asyncio.to_thread(_walk_upstream_columns, full_name, col, max_hops)
        return {"path": path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/run-failures")
async def run_failures(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    window_hours: int = Query(24),
):
    """Return recent producer run failures within a time window of the target table."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        full_name = f"{c}.{s}.{t}"
        failures = await asyncio.to_thread(_get_failed_runs_around, full_name, None, window_hours)
        return {"failures": failures}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
