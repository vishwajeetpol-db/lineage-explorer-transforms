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

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py
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


# ---------------------------------------------------------------------------
# Extended AI/ML Lineage: Feature Store, Vector Search, Prompt Lineage
# ---------------------------------------------------------------------------

@router.get("/feature-tables")
async def list_feature_tables(request: Request, catalog: Optional[str] = Query(None)):
    """List Feature Store tables (online tables + feature specs)."""
    try:
        where = f"WHERE table_catalog = '{catalog}'" if catalog else ""
        rows = _execute_sql(f"""
            SELECT table_catalog, table_schema, table_name, table_type, comment
            FROM system.information_schema.tables
            {where}
            AND (lower(comment) LIKE '%feature%' OR lower(table_name) LIKE '%feature%')
            ORDER BY table_catalog, table_schema, table_name
            LIMIT 200
        """)
        return {"feature_tables": rows}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/vector-indexes")
async def list_vector_indexes(request: Request):
    """List Vector Search indexes and their source tables."""
    try:
        # Query the system tables for vector search indexes
        rows = _execute_sql("""
            SELECT
                index_name, primary_key, endpoint_name,
                index_type, source_table
            FROM system.information_schema.vector_search_indexes
            ORDER BY index_name
            LIMIT 200
        """)
        return {"vector_indexes": rows}
    except Exception as e:
        # Vector search system table may not exist in all workspaces
        logger.debug(f"Vector index query failed (non-fatal): {e}")
        return {"vector_indexes": [], "note": "Vector search system tables not available"}


@router.get("/vector-lineage")
async def vector_index_lineage(request: Request, index_name: str = Query(...)):
    """Get lineage for a vector search index: source table → index → serving endpoint."""
    if not _NAME_RE.match(index_name):
        raise HTTPException(status_code=400, detail="Invalid index_name")
    try:
        rows = _execute_sql(f"""
            SELECT index_name, source_table, endpoint_name, primary_key, index_type
            FROM system.information_schema.vector_search_indexes
            WHERE index_name = '{index_name.replace(chr(39), chr(39)*2)}'
        """)
        if not rows:
            return {"lineage": None, "note": "Index not found"}
        idx = rows[0]
        return {
            "lineage": {
                "source_table": idx.get("source_table"),
                "index_name": idx.get("index_name"),
                "endpoint_name": idx.get("endpoint_name"),
                "index_type": idx.get("index_type"),
                "primary_key": idx.get("primary_key"),
            }
        }
    except Exception as e:
        logger.debug(f"Vector lineage failed: {e}")
        return {"lineage": None, "note": str(e)}


@router.get("/prompt-lineage")
async def prompt_lineage(request: Request, endpoint_name: str = Query(...)):
    """Get prompt/inference lineage for a serving endpoint:
    which tables feed it (via vector indexes or direct), and usage stats."""
    if not _NAME_RE.match(endpoint_name):
        raise HTTPException(status_code=400, detail="Invalid endpoint_name")
    try:
        # Get vector indexes feeding this endpoint
        vector_sources = []
        try:
            rows = _execute_sql(f"""
                SELECT index_name, source_table
                FROM system.information_schema.vector_search_indexes
                WHERE endpoint_name = '{endpoint_name.replace(chr(39), chr(39)*2)}'
            """)
            vector_sources = rows
        except Exception:
            pass

        # Get model→table lineage for this endpoint
        model_sources = get_models_for_table("", "", "")  # Will be filtered below

        # Get endpoint usage
        usage = get_endpoint_usage(endpoint_name)

        return {
            "endpoint": endpoint_name,
            "vector_sources": vector_sources,
            "usage": usage,
            "lineage_type": "rag" if vector_sources else "model_serving",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/inference-tables")
async def list_inference_tables(request: Request):
    """List inference/payload logging tables from serving endpoints."""
    try:
        rows = _execute_sql("""
            SELECT DISTINCT
                endpoint_name, inference_table_name
            FROM system.serving.served_entities
            WHERE inference_table_name IS NOT NULL
            ORDER BY endpoint_name
            LIMIT 200
        """)
        return {"inference_tables": rows}
    except Exception as e:
        logger.debug(f"Inference tables query failed: {e}")
        return {"inference_tables": [], "note": "Serving system tables not available"}


def _execute_sql(sql: str) -> list[dict]:
    """Local SQL executor for ML routes."""
    import os
    from databricks.sdk.service.sql import StatementState
    wh = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if not wh:
        raise RuntimeError("No SQL warehouse available.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=wh, wait_timeout=os.environ.get("SQL_WAIT_TIMEOUT", "50s"),
    )
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


logger = __import__("logging").getLogger(__name__)
