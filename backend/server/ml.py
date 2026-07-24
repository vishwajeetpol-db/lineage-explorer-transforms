"""AI/ML lineage — capability 21.

Surfaces two complementary views:

  1. Model Serving endpoint inventory — from system.serving.served_entities
     (DBR 14+). Shows which endpoints are live, their served model versions,
     and recent call counts.

  2. Model → training-data lineage — from the model_lineage app-owned table
     (written by the user's model-training notebooks via the optional
     `register_model_lineage()` helper). Shows which UC table(s) a given
     registered model was trained on and the notebook that ran the job.

All functions are non-fatal.
"""
from __future__ import annotations

import os
import json
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
MODEL_LINEAGE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.model_lineage"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
ML_LOOKBACK_DAYS = int(os.environ.get("ML_LOOKBACK_DAYS", "30"))


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
    )
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def _ensure_model_lineage_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {MODEL_LINEAGE_TABLE} ("
            f"  model_name STRING,"
            f"  model_version STRING,"
            f"  training_table STRING,"
            f"  training_catalog STRING,"
            f"  training_schema STRING,"
            f"  training_table_name STRING,"
            f"  job_id STRING,"
            f"  run_id STRING,"
            f"  notebook_path STRING,"
            f"  registered_by STRING,"
            f"  registered_at TIMESTAMP,"
            f"  notes STRING"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"ml: could not ensure {MODEL_LINEAGE_TABLE} (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Serving endpoint inventory
# ---------------------------------------------------------------------------

def list_serving_endpoints() -> list[dict]:
    """Return all Model Serving endpoints from system.serving.served_entities."""
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  endpoint_name, "
            f"  entity_name, "
            f"  entity_version, "
            f"  entity_type, "
            f"  creator, "
            f"  creation_timestamp, "
            f"  state "
            f"FROM system.serving.served_entities "
            f"ORDER BY creation_timestamp DESC "
            f"LIMIT 200"
        )
        return rows
    except Exception as e:
        logger.info(f"ml: could not query system.serving.served_entities: {e}")
        return []


def get_endpoint_usage(endpoint_name: str) -> list[dict]:
    """Return recent request counts for a serving endpoint."""
    safe = endpoint_name.replace("'", "")
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  DATE_TRUNC('DAY', timestamp) AS day, "
            f"  COUNT(*) AS request_count, "
            f"  SUM(CASE WHEN status_code >= 400 THEN 1 ELSE 0 END) AS error_count "
            f"FROM system.serving.endpoint_usage "
            f"WHERE endpoint_name = '{safe}' "
            f"  AND timestamp >= dateadd(DAY, -{ML_LOOKBACK_DAYS}, current_timestamp()) "
            f"GROUP BY 1 ORDER BY 1 DESC LIMIT 60"
        )
        return rows
    except Exception as e:
        logger.info(f"ml: endpoint usage unavailable for {endpoint_name}: {e}")
        return []


# ---------------------------------------------------------------------------
# Model → training-data lineage
# ---------------------------------------------------------------------------

def _run_input_tables(client, run_id: str) -> list[str]:
    """UC tables a training run consumed, from its MLflow dataset_inputs."""
    if not run_id:
        return []
    try:
        resp = client.api_client.do("GET", "/api/2.0/mlflow/runs/get", query={"run_id": run_id})
    except Exception:
        return []
    tables: list[str] = []
    for di in ((resp.get("run", {}).get("inputs", {}) or {}).get("dataset_inputs", []) or []):
        src = di.get("dataset", {}).get("source")
        if not src:
            continue
        try:
            s = json.loads(src) if isinstance(src, str) else src
            t = s.get("table_name")
            if t:
                tables.append(t)
        except Exception:
            continue
    return tables


def _endpoints_for_model(client, model_fqn: str) -> list[str]:
    """Serving endpoints currently serving a model FQN (live, from system.serving)."""
    safe = model_fqn.replace("'", "")
    try:
        rows = _execute_sql(
            f"SELECT DISTINCT endpoint_name FROM system.serving.served_entities "
            f"WHERE endpoint_delete_time IS NULL AND entity_name = '{safe}'"
        )
        return [r["endpoint_name"] for r in rows if r.get("endpoint_name")]
    except Exception:
        return []


def _derive_models_for_table_live(catalog: str, schema: str, table: str) -> list[dict]:
    """Derive 'models trained on this table' live from the UC Model Registry +
    MLflow run inputs — no app-owned registry table or manual populate needed.

    Walks: registered models in the schema → each version's training run →
    that run's input datasets. A model version whose run consumed `full_name`
    is reported as trained on this table. (UC doesn't record model→table edges
    in system.access.table_lineage, so this reverse walk is the real source.)
    """
    full_name = f"{catalog}.{schema}.{table}"
    client = _get_client()
    out: list[dict] = []
    try:
        # Registered models live in a schema; scan the target table's schema
        # (models are typically registered alongside the data they're built on).
        resp = client.api_client.do(
            "GET", "/api/2.1/unity-catalog/models",
            query={"catalog_name": catalog, "schema_name": schema, "max_results": 400},
        )
        models = resp.get("registered_models", []) or []
    except Exception as e:
        logger.info(f"ml: could not list registered models in {catalog}.{schema}: {e}")
        return []

    for m in models:
        model_fqn = m.get("full_name")
        if not model_fqn:
            continue
        try:
            vresp = client.api_client.do(
                "GET", f"/api/2.1/unity-catalog/models/{model_fqn}/versions",
                query={"max_results": 100},
            )
            versions = vresp.get("model_versions", []) or []
        except Exception:
            continue
        for v in versions:
            run_id = v.get("run_id")
            inputs = _run_input_tables(client, run_id)
            if full_name in inputs:
                out.append({
                    "model_name": model_fqn,
                    "model_version": str(v.get("version") or ""),
                    "run_id": run_id,
                    "job_id": None,
                    "notebook_path": None,
                    "registered_by": v.get("created_by"),
                    "endpoints": _endpoints_for_model(client, model_fqn),
                })
    return out


def get_models_for_table(catalog: str, schema: str, table: str) -> list[dict]:
    """Return registered models trained on `catalog.schema.table`.

    Prefers a LIVE derivation from the UC Model Registry + MLflow run inputs
    (works with zero setup); falls back to the app-owned model_lineage table for
    any edges an operator registered explicitly via register_model_lineage()."""
    full_name = f"{catalog}.{schema}.{table}"

    # 1. Live derivation (primary).
    try:
        live = _derive_models_for_table_live(catalog, schema, table)
        if live:
            return live
    except Exception as e:
        logger.info(f"ml: live model derivation failed for {full_name}: {e}")

    # 2. App-owned registry table (fallback / explicit registrations).
    try:
        _ensure_model_lineage_table()
        rows = _execute_sql(
            f"SELECT model_name, model_version, job_id, run_id, notebook_path, "
            f"       registered_by, registered_at, notes "
            f"FROM {MODEL_LINEAGE_TABLE} "
            f"WHERE training_table = '{full_name}' "
            f"ORDER BY registered_at DESC "
            f"LIMIT 100"
        )
        return rows
    except Exception as e:
        logger.info(f"ml: model lineage unavailable for {full_name}: {e}")
        return []


def get_table_for_model(model_name: str, model_version: Optional[str] = None) -> list[dict]:
    """Return which training tables a given registered model was built from."""
    safe_name = model_name.replace("'", "")
    version_filter = f"AND model_version = '{model_version}'" if model_version else ""
    try:
        _ensure_model_lineage_table()
        rows = _execute_sql(
            f"SELECT training_table, model_version, job_id, run_id, notebook_path, "
            f"       registered_by, registered_at "
            f"FROM {MODEL_LINEAGE_TABLE} "
            f"WHERE model_name = '{safe_name}' {version_filter} "
            f"ORDER BY registered_at DESC "
            f"LIMIT 50"
        )
        return rows
    except Exception as e:
        logger.info(f"ml: training-data lineage unavailable for model {model_name}: {e}")
        return []


def register_model_lineage(
    model_name: str,
    model_version: str,
    training_table: str,
    job_id: str = "",
    run_id: str = "",
    notebook_path: str = "",
    actor: str = "",
    notes: str = "",
) -> dict:
    """Record a model → training-table lineage row. Called by training notebooks."""
    _ensure_model_lineage_table()
    safe = lambda s: (s or "").replace("'", "")
    parts = training_table.split(".")
    tc = parts[0] if len(parts) > 0 else ""
    ts = parts[1] if len(parts) > 1 else ""
    tt = parts[2] if len(parts) > 2 else training_table
    _execute_sql(
        f"INSERT INTO {MODEL_LINEAGE_TABLE} VALUES ("
        f"'{safe(model_name)}', '{safe(model_version)}', '{safe(training_table)}', "
        f"'{safe(tc)}', '{safe(ts)}', '{safe(tt)}', "
        f"'{safe(job_id)}', '{safe(run_id)}', '{safe(notebook_path)}', "
        f"'{safe(actor)}', current_timestamp(), '{safe(notes)}')"
    )
    return {"status": "registered", "model_name": model_name, "training_table": training_table}
