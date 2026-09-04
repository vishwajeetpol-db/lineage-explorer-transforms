"""Routes for Access & security lineage — capability 20.

Endpoints:
  GET /api/access          — declared grants + empirical audit access + dormant grants (admin)
  GET /api/access/schema   — schema-level empirical access summary (admin)

BOTH ARE ADMIN-GATED, and that is a security boundary rather than a convenience.
These responses are built from `system.access.audit` and are keyed on
`user_identity.email` — they answer "which named person read this table, how often,
and when". Every other panel in the app describes DATA; this one describes PEOPLE.

Combined with the app's single-service-principal query model (every user sees
whatever the app SP can read — see APP_SP_VISIBILITY_NOTE in edge_case_guards), an
ungated version let any user of the app enumerate colleagues' access patterns across
the whole workspace. In most enterprises that is a restricted governance role, and in
several regulated sectors exposing it broadly is itself the audit finding.

Gating rather than redacting is deliberate: the panel's own purpose — declared grants
versus empirical use, and dormant-grant analysis — is a governance task whose answer
is the identity. A version with the identities removed would not be a lesser version
of this panel, it would be a different and largely useless one.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query, Request

from backend.server.access import get_access_summary, get_schema_access_summary
from backend.server.capability_cache import serve_or_compute
from backend.validators import require_admin

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
    email = require_admin(request)
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    fqn = f"{c}.{s}.{t}"
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
    require_admin(request)
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    try:
        return {"tables": get_schema_access_summary(c, s)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
