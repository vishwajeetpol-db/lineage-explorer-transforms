"""Routes for AI/ML lineage — capability 21.

Endpoints:
  GET  /api/ml/endpoints               — Model Serving endpoint inventory
  GET  /api/ml/endpoints/{name}/usage  — daily request/error counts
  GET  /api/ml/models-for-table        — models trained on a given UC table
  GET  /api/ml/tables-for-model        — training tables for a given model
  POST /api/ml/register-lineage        — record model→training-table lineage
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from typing import Optional

from backend.lineage_service import _get_client
from backend.server.ml import (
    list_serving_endpoints,
    get_endpoint_usage,
    get_models_for_table,
    get_table_for_model,
    register_model_lineage,
)
from backend.validators import sql_str

logger = logging.getLogger(__name__)

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
        logger.error(f"ml: serving endpoint inventory failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list serving endpoints")


@router.get("/endpoints/{endpoint_name}/usage")
async def endpoint_usage(request: Request, endpoint_name: str):
    """Return daily request/error counts for a serving endpoint."""
    if not _NAME_RE.match(endpoint_name):
        raise HTTPException(status_code=400, detail="Invalid endpoint_name")
    try:
        return {"usage": get_endpoint_usage(endpoint_name)}
    except Exception as e:
        logger.error(f"ml: endpoint usage failed for {endpoint_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load endpoint usage")


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
        logger.error(f"ml: models-for-table failed for {c}.{s}.{t}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load models for table")


@router.get("/tables-for-model")
async def tables_for_model(
    request: Request,
    model_name: str = Query(...),
    model_version: Optional[str] = Query(None),
):
    """Return training tables for a registered model."""
    if not _NAME_RE.match(model_name):
        raise HTTPException(status_code=400, detail="Invalid model_name")
    # model_version reaches a WHERE clause too and must clear the same allow-list.
    # Without this it was an in-band UNION injection: _NAME_RE rejects both `'`
    # and `\`, so a version that matches cannot break out of the SQL literal.
    # An empty value is "not supplied" here, exactly as get_table_for_model reads
    # it, so validation and use agree on which values reach the query.
    if model_version and not _NAME_RE.match(model_version):
        raise HTTPException(status_code=400, detail="Invalid model_version")
    try:
        return {"tables": get_table_for_model(model_name, model_version)}
    except Exception as e:
        logger.error(f"ml: tables-for-model failed for {model_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load tables for model")


@router.post("/register-lineage")
async def register_ml_lineage(request: Request, body: ModelLineageIn):
    """Record a model→training-table lineage row from a training notebook."""
    # Deliberately NOT admin-gated: training notebooks run as their own author,
    # and this is append-only and stamps the caller in registered_by, so the
    # worst a non-admin can do is add an attributable row. Gating it here would
    # break the register_model_lineage() helper for every non-admin data scientist.
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
        logger.error(f"ml: register-lineage failed for {body.model_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to register model lineage")


# ---------------------------------------------------------------------------
# Extended AI/ML Lineage: Feature Store, Vector Search, Prompt Lineage
# ---------------------------------------------------------------------------

@router.get("/feature-tables")
async def list_feature_tables(request: Request, catalog: Optional[str] = Query(None)):
    """List Feature Store tables (online tables + feature specs).

    `catalog` is a UC identifier, so it goes through the _validate allow-list
    (outside the try, so its 400 is not re-wrapped as a 500). The filter is then
    appended to a `WHERE 1=1` stub: the previous `{where}`-then-`AND` shape
    produced a bare `AND` with no WHERE whenever catalog was omitted, which was a
    syntax error, so this endpoint never returned rows for the unfiltered case.
    """
    cat = _validate(catalog, "catalog") if catalog else ""
    try:
        cat_filter = f"AND table_catalog = '{sql_str(cat)}'" if cat else ""
        rows = _execute_sql(f"""
            SELECT table_catalog, table_schema, table_name, table_type, comment
            FROM system.information_schema.tables
            WHERE 1=1 {cat_filter}
            AND (lower(comment) LIKE '%feature%' OR lower(table_name) LIKE '%feature%')
            ORDER BY table_catalog, table_schema, table_name
            LIMIT 200
        """)
        return {"feature_tables": rows}
    except Exception as e:
        logger.error(f"ml: feature-tables query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list feature tables")


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
        # Vector search system table may not exist in all workspaces. Logged at
        # warning, not debug: this handler swallowed a NameError from the missing
        # _get_client import for its whole life and reported it as "no rows".
        logger.warning(f"ml: vector index query failed (non-fatal): {e}")
        return {"vector_indexes": [], "note": "Vector search system tables not available"}


@router.get("/vector-lineage")
async def vector_index_lineage(request: Request, index_name: str = Query(...)):
    """Get lineage for a vector search index: source table → index → serving endpoint."""
    # The anchored _NAME_RE allow-list is what makes this safe: it permits neither
    # `'` nor `\`, so no value that reaches the query can terminate the literal.
    # sql_str is defence in depth for the day that regex is loosened.
    if not _NAME_RE.match(index_name):
        raise HTTPException(status_code=400, detail="Invalid index_name")
    try:
        rows = _execute_sql(f"""
            SELECT index_name, source_table, endpoint_name, primary_key, index_type
            FROM system.information_schema.vector_search_indexes
            WHERE index_name = '{sql_str(index_name)}'
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
        # Non-fatal (same missing-system-table condition as /vector-indexes), but
        # the exception text stays server-side: it names system tables and SQL
        # state the caller has no business seeing.
        logger.warning(f"ml: vector lineage failed for {index_name}: {e}")
        return {"lineage": None, "note": "Vector search system tables not available"}


@router.get("/prompt-lineage")
async def prompt_lineage(request: Request, endpoint_name: str = Query(...)):
    """Get prompt/inference lineage for a serving endpoint:
    which tables feed it (via vector indexes or direct), and usage stats."""
    # As in /vector-lineage: the anchored _NAME_RE allow-list (no `'`, no `\`) is
    # the control; sql_str below is defence in depth.
    if not _NAME_RE.match(endpoint_name):
        raise HTTPException(status_code=400, detail="Invalid endpoint_name")
    try:
        # Get vector indexes feeding this endpoint
        vector_sources = []
        try:
            rows = _execute_sql(f"""
                SELECT index_name, source_table
                FROM system.information_schema.vector_search_indexes
                WHERE endpoint_name = '{sql_str(endpoint_name)}'
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
        logger.error(f"ml: prompt lineage failed for {endpoint_name}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load prompt lineage")


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
        logger.warning(f"ml: inference tables query failed (non-fatal): {e}")
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
