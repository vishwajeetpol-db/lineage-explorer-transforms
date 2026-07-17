"""Routes for AI/ML lineage — capability 21.

Endpoints:
  GET  /api/ml/endpoints               — Model Serving endpoint inventory
  GET  /api/ml/endpoints/{name}/usage  — daily request/error counts
  GET  /api/ml/models-for-table        — models trained on a given UC table
  GET  /api/ml/tables-for-model        — training tables for a given model
  POST /api/ml/register-lineage        — record model→training-table lineage
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional

from backend.server.ml import (
    list_serving_endpoints,
    get_endpoint_usage,
    get_models_for_table,
    get_table_for_model,
    register_model_lineage,
)

router = APIRouter(prefix="/api/ml", tags=["ml"])

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")
_NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_./ -]{1,256}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


class ModelLineageIn(BaseModel):
    model_name: str
    model_version: str
    training_table: str
    job_id: Optional[str] = ""
    run_id: Optional[str] = ""
    notebook_path: Optional[str] = ""
    notes: Optional[str] = ""


@router.get("/endpoints")
async def serving_endpoints(request: Request):
    """List all Model Serving endpoints from system.serving."""
    try:
        return {"endpoints": list_serving_endpoints()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/endpoints/{endpoint_name}/usage")
async def endpoint_usage(request: Request, endpoint_name: str):
    """Return daily request/error counts for a serving endpoint."""
    if not _NAME_RE.match(endpoint_name):
        raise HTTPException(status_code=400, detail="Invalid endpoint_name")
    try:
        return {"usage": get_endpoint_usage(endpoint_name)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/models-for-table")
async def models_for_table(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Return registered models trained on `catalog.schema.table`."""
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    try:
        return {"models": get_models_for_table(c, s, t)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tables-for-model")
async def tables_for_model(
    request: Request,
    model_name: str = Query(...),
    model_version: Optional[str] = Query(None),
):
    """Return training tables for a registered model."""
    if not _NAME_RE.match(model_name):
        raise HTTPException(status_code=400, detail="Invalid model_name")
    try:
        return {"tables": get_table_for_model(model_name, model_version)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/register-lineage")
async def register_ml_lineage(request: Request, body: ModelLineageIn):
    """Record a model→training-table lineage row from a training notebook."""
    from backend.main import _get_user_info
    email, _ = _get_user_info(request)
    try:
        result = register_model_lineage(
            model_name=body.model_name,
            model_version=body.model_version,
            training_table=body.training_table,
            job_id=body.job_id or "",
            run_id=body.run_id or "",
            notebook_path=body.notebook_path or "",
            actor=email or "unknown",
            notes=body.notes or "",
        )
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
