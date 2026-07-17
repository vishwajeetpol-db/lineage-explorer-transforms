"""Routes for Governance & classification (capability 17).

Endpoints:
  GET  /api/governance              — ownership + column sensitivity for a table
  GET  /api/governance/propagation  — downstream columns inheriting sensitivity
  GET  /api/governance/config       — list all user-defined classification rules
  POST /api/governance/config       — upsert a classification rule (admin)
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional

from backend.server.governance import (
    get_table_governance,
    get_downstream_sensitivity_propagation,
    list_governance_rules,
    upsert_governance_rule,
)

router = APIRouter(prefix="/api/governance", tags=["governance"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


class GovernanceRuleIn(BaseModel):
    rule_id: Optional[str] = None
    catalog: Optional[str] = None
    schema_name: Optional[str] = None
    table_pattern: Optional[str] = None
    column_pattern: Optional[str] = None
    tag_name: Optional[str] = None
    sensitivity: str = "SENSITIVE"
    notes: Optional[str] = None


@router.get("")
async def governance_for_table(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return ownership + column sensitivity classification for a table."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return get_table_governance(c, s, t)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/propagation")
async def governance_propagation(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return downstream columns that inherit sensitivity from this table."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return {"propagation": get_downstream_sensitivity_propagation(c, s, t)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config")
async def list_governance_config(request: Request):
    """Return all user-defined classification rules."""
    return {"rules": list_governance_rules()}


@router.post("/config")
async def upsert_governance_config(request: Request, rule: GovernanceRuleIn):
    """Upsert a classification rule. Admin-gated."""
    # Import the admin-gate helper from main to reuse the existing pattern
    from backend.main import _get_user_info
    _email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to modify governance rules.")
    rule_dict = rule.model_dump()
    rule_dict["schema"] = rule_dict.pop("schema_name", None)
    try:
        return upsert_governance_rule(rule_dict, actor=_email or "unknown")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
