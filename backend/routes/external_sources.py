"""Routes for Multi-Platform / External Sources — capability 05.

Ingests lineage from external platforms (dbt, Airflow, Snowflake, BigQuery)
via manifest files, API logs, or manual registration.

Endpoints:
  POST /api/external/dbt/import         — import a dbt manifest.json
  POST /api/external/airflow/import     — import Airflow DAG lineage
  POST /api/external/register           — manually register an external source/target
  GET  /api/external/sources            — list all registered external sources
  DELETE /api/external/sources/{id}     — remove an external source
  GET  /api/external/lineage            — get external lineage edges for a table

  # OL Producer Bridge — closes #05 to HAVE (v2.5.3)
  POST /api/external/ol-bridge/register          — register an OL-emitting platform + get receive URL
  POST /api/external/ol-bridge/ingest/{source_id} — receive endpoint for external OL pushes
  GET  /api/external/ol-bridge/sources           — list bridge sources with push stats
  GET  /api/external/ol-bridge/events            — inspect received OL events

Persisted in app-owned Delta tables:
  - external_sources (source_id, platform, name, connection_info, ...)
  - external_lineage_edges (edge_id, source_platform, source_asset, target_asset, ...)
  - external_ol_bridge_sources (source_id, platform, name, receive_token, ...)
  - external_ol_bridge_events (event_id, source_id, platform, job_name, ...)
"""
from __future__ import annotations

import os
import uuid
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.validators import require_admin, sql_str

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/external", tags=["external-sources"])

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
SOURCES_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.external_sources"
EDGES_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.external_lineage_edges"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available.")
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


def _ensure_tables() -> None:
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {SOURCES_TABLE} (
            source_id STRING, platform STRING, name STRING, description STRING,
            connection_info STRING, metadata STRING,
            created_by STRING, created_at TIMESTAMP, updated_at TIMESTAMP
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {EDGES_TABLE} (
            edge_id STRING, source_platform STRING, source_asset STRING,
            source_asset_type STRING, target_asset STRING, target_asset_type STRING,
            relationship STRING, transformation STRING, confidence STRING,
            created_at TIMESTAMP, metadata STRING
        ) USING DELTA""")
    except Exception as e:
        logger.warning(f"external_sources: could not ensure tables: {e}")


_tables_ensured = False


def _lazy_ensure():
    global _tables_ensured
    if not _tables_ensured:
        _ensure_tables()
        _tables_ensured = True


# Every platform token this module accepts from a caller: the union of the two
# request models' documented enums below plus the OL-native emitters named in the
# bridge notes further down. Both the write paths and the read filters run values
# through _validate_platform, so a stored platform is always one of these and a
# filter value can never carry SQL text (rejected with 400, not escaped).
_ALLOWED_PLATFORMS = frozenset({
    "dbt", "dbt_cloud", "airflow", "snowflake", "bigquery", "oracle",
    "spark", "flink", "trino", "great_expectations", "custom",
})


def _validate_platform(value: str, field: str = "platform") -> str:
    """Lower-case and allow-list a platform token, else HTTP 400."""
    v = (value or "").strip().lower()
    if v not in _ALLOWED_PLATFORMS:
        raise HTTPException(status_code=400, detail=f"Invalid {field}: '{(value or '')[:50]}'")
    return v


class ExternalSourceIn(BaseModel):
    platform: str  # dbt | airflow | snowflake | bigquery | oracle | custom
    name: str
    description: Optional[str] = ""
    connection_info: Optional[str] = ""  # JSON string with connection metadata


class ExternalEdgeIn(BaseModel):
    source_platform: str
    source_asset: str  # fully qualified name in source platform
    source_asset_type: Optional[str] = "table"  # table | view | model | task
    target_asset: str  # UC table FQN (catalog.schema.table)
    target_asset_type: Optional[str] = "table"
    relationship: Optional[str] = "produces"  # produces | consumes | transforms
    transformation: Optional[str] = ""  # SQL or description of transform
    confidence: Optional[str] = "manual"  # manual | inferred | verified


@router.post("/dbt/import")
async def import_dbt_manifest(request: Request, body: dict):
    """Import a dbt manifest.json to register lineage from dbt models.

    Parses the manifest to extract:
    - Model definitions and their source dependencies
    - Column-level lineage from model SQL
    - Test definitions as DQ rules

    A2 FIX: Admin-gated — imports inject external lineage edges into the graph.
    """
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to import external lineage")
    _lazy_ensure()
    manifest = body.get("manifest", {})
    if not manifest:
        raise HTTPException(status_code=400, detail="No manifest provided")

    nodes = manifest.get("nodes", {})
    sources = manifest.get("sources", {})
    project_name = manifest.get("metadata", {}).get("project_id", "unknown_project")

    imported_edges = 0
    imported_sources = 0
    now = datetime.now(timezone.utc).isoformat()

    try:
        # Register dbt as an external source
        src_id = str(uuid.uuid4())
        safe_project = sql_str(project_name, 300)
        _execute_sql(f"""
            MERGE INTO {SOURCES_TABLE} t
            USING (SELECT 'dbt' AS platform, '{safe_project}' AS name) s
            ON t.platform = s.platform AND t.name = s.name
            WHEN NOT MATCHED THEN INSERT (source_id, platform, name, description, created_by, created_at, updated_at)
            VALUES ('{src_id}', 'dbt', '{safe_project}', 'Imported from dbt manifest',
                    'app', TIMESTAMP '{now}', TIMESTAMP '{now}')
            WHEN MATCHED THEN UPDATE SET updated_at = TIMESTAMP '{now}'
        """)
        imported_sources += 1

        # Parse model nodes for lineage
        for node_id, node in nodes.items():
            if node.get("resource_type") not in ("model", "snapshot"):
                continue

            model_name = sql_str(node.get("name", ""), 255)
            # Get the target table (if materialized to a UC table)
            db = sql_str(node.get("database", ""), 255)
            schema_name = sql_str(node.get("schema", ""), 255)
            target_fqn = f"{db}.{schema_name}.{model_name}"

            # Extract upstream dependencies
            depends_on = node.get("depends_on", {}).get("nodes", [])
            for dep_id in depends_on:
                dep_node = nodes.get(dep_id) or sources.get(dep_id)
                if not dep_node:
                    continue

                dep_db = sql_str(dep_node.get("database", ""), 255)
                dep_schema = sql_str(dep_node.get("schema", ""), 255)
                dep_name = sql_str(dep_node.get("name", ""), 255)
                source_fqn = f"{dep_db}.{dep_schema}.{dep_name}"

                edge_id = str(uuid.uuid4())
                raw_sql = sql_str(node.get("raw_sql", "") or node.get("raw_code", ""), 2000)

                _execute_sql(f"""
                    INSERT INTO {EDGES_TABLE}
                    (edge_id, source_platform, source_asset, source_asset_type, target_asset, target_asset_type,
                     relationship, transformation, confidence, created_at)
                    VALUES ('{edge_id}', 'dbt', '{source_fqn}', 'model', '{target_fqn}', 'model',
                            'transforms', '{raw_sql}', 'verified', TIMESTAMP '{now}')
                """)
                imported_edges += 1

        return {
            "status": "ok",
            "project": project_name,
            "imported_sources": imported_sources,
            "imported_edges": imported_edges,
            "models_processed": len([n for n in nodes.values() if n.get("resource_type") == "model"]),
        }
    except Exception as e:
        # Detail stays server-side: the raw text is SQL/SDK error output.
        logger.error(f"dbt manifest import failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to import dbt manifest")


@router.post("/airflow/import")
async def import_airflow_lineage(request: Request, body: dict):
    """Import Airflow DAG lineage from the Airflow REST API response format.

    A2 FIX: Admin-gated — imports inject external lineage edges.
    """
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to import external lineage")
    _lazy_ensure()
    dags = body.get("dags", [])
    if not dags:
        raise HTTPException(status_code=400, detail="No DAGs provided")

    imported = 0
    now = datetime.now(timezone.utc).isoformat()

    try:
        for dag in dags[:50]:  # Cap at 50 DAGs per import
            # Raw values feed the metadata JSON; the *_safe copies go into SQL.
            # Escaping before json.dumps would double-escape the stored JSON.
            raw_dag_id = str(dag.get("dag_id", "unknown"))[:200]
            dag_id = sql_str(raw_dag_id)
            tasks = dag.get("tasks", [])

            for task in tasks:
                raw_task_id = str(task.get("task_id", ""))[:200]
                task_id = sql_str(raw_task_id)
                metadata_json = sql_str(
                    json.dumps({"dag_id": raw_dag_id, "task_id": raw_task_id})
                )
                inlets = task.get("inlets", [])  # Input datasets
                outlets = task.get("outlets", [])  # Output datasets

                for inlet in inlets:
                    for outlet in outlets:
                        edge_id = str(uuid.uuid4())
                        _execute_sql(f"""
                            INSERT INTO {EDGES_TABLE}
                            (edge_id, source_platform, source_asset, source_asset_type, target_asset, target_asset_type,
                             relationship, transformation, confidence, created_at, metadata)
                            VALUES ('{edge_id}', 'airflow',
                                    '{sql_str(inlet, 500)}', 'dataset',
                                    '{sql_str(outlet, 500)}', 'dataset',
                                    'transforms', '{dag_id}/{task_id}', 'verified',
                                    TIMESTAMP '{now}', '{metadata_json}')
                        """)
                        imported += 1

        return {"status": "ok", "imported_edges": imported}
    except Exception as e:
        logger.error(f"airflow lineage import failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to import Airflow lineage")


@router.post("/register")
async def register_external_edge(request: Request, body: ExternalEdgeIn):
    """Manually register a single external lineage edge.

    Admin-gated: this writes external_lineage_edges — the same table and the same
    action as /dbt/import and /airflow/import. Non-admins should not be able to
    inject lineage edges that later render as trusted graph edges.
    """
    require_admin(request)
    _lazy_ensure()
    edge_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    platform = _validate_platform(body.source_platform, "source_platform")
    try:
        _execute_sql(f"""
            INSERT INTO {EDGES_TABLE}
            (edge_id, source_platform, source_asset, source_asset_type, target_asset, target_asset_type,
             relationship, transformation, confidence, created_at)
            VALUES ('{edge_id}', '{platform}',
                    '{sql_str(body.source_asset, 500)}', '{sql_str(body.source_asset_type or "table", 100)}',
                    '{sql_str(body.target_asset, 500)}', '{sql_str(body.target_asset_type or "table", 100)}',
                    '{sql_str(body.relationship or "produces", 100)}',
                    '{sql_str(body.transformation or "", 2000)}',
                    '{sql_str(body.confidence or "manual", 100)}', TIMESTAMP '{now}')
        """)
        return {"status": "ok", "edge_id": edge_id}
    except Exception as e:
        logger.error(f"external edge registration failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to register external edge")


@router.get("/sources")
async def list_sources(request: Request):
    """List registered external sources.

    Open to all app users, but NEVER `SELECT *`: `connection_info` holds
    connection metadata for the external platform (hosts, accounts, and
    potentially credentials), and this endpoint is ungated. Select columns
    explicitly so a future column added to the table isn't published by default.
    """
    _lazy_ensure()
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"SELECT source_id, platform, name, description, metadata, "
            f"       created_by, created_at, updated_at "
            f"FROM {SOURCES_TABLE} ORDER BY platform, name",
        )
        return {"sources": rows}
    except Exception as e:
        logger.error(f"external source list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list external sources")


@router.delete("/sources/{source_id}")
async def delete_source(request: Request, source_id: str):
    """Delete a registered external source.

    Admin-gated: a hard DELETE with no per-user scoping, mirroring the gate on
    the /register write into the same set of app-owned tables.
    """
    require_admin(request)
    _lazy_ensure()
    safe_id = sql_str(source_id, 100)
    try:
        await asyncio.to_thread(_execute_sql, f"DELETE FROM {SOURCES_TABLE} WHERE source_id = '{safe_id}'")
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"external source delete failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete external source")


@router.get("/lineage")
async def get_external_lineage(
    request: Request,
    table_fqn: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Get external lineage edges, optionally filtered by table or platform."""
    _lazy_ensure()
    conditions = ["1=1"]
    if table_fqn:
        safe_fqn = sql_str(table_fqn, 300)
        conditions.append(f"(source_asset = '{safe_fqn}' OR target_asset = '{safe_fqn}')")
    if platform:
        conditions.append(f"source_platform = '{_validate_platform(platform)}'")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql, f"SELECT * FROM {EDGES_TABLE} WHERE {where} ORDER BY created_at DESC LIMIT {limit}"
        )
        return {"edges": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"external lineage query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch external lineage")


# ===========================================================================
# OpenLineage Producer Bridge — closes #05 Multi-Platform to HAVE (v2.5.3)
#
# Problem: PARTIAL status came from only exposing foreign catalogs as boundary
# nodes. In-platform expression tracing inside Snowflake/BigQuery is out of
# scope — but we don't need it. Platforms that natively emit OpenLineage
# (Snowflake Horizon, BigQuery lineage API via OL adapter, Apache Spark/Flink
# with the openlineage-spark integration, dbt Cloud, etc.) can push their
# events directly to us. This gives genuine cross-platform lineage without
# requiring us to own any in-platform compute.
#
# Architecture:
#   1. Admin registers an external OL-emitting system via /ol-bridge/register.
#      Returns a source_id that doubles as the receive token + the push URL.
#   2. External system is configured to POST standard OpenLineage RunEvents to
#      POST /api/external/ol-bridge/ingest/{source_id}.
#   3. Events are stored in external_ol_bridge_events, tagged by platform.
#   4. GET /api/external/ol-bridge/sources shows all registered platforms +
#      push statistics (last_push_at, total_events).
#   5. GET /api/external/ol-bridge/events lets operators inspect received edges.
#
# Supported OL-native emitters (no custom adapter needed):
#   Snowflake Horizon (OL transport), Apache Spark + openlineage-spark,
#   Apache Flink + openlineage-flink, dbt Core/Cloud, Airflow (2.7+ native OL),
#   Great Expectations, Trino, BigQuery via OL Proxy sidecar.
# ===========================================================================

OL_BRIDGE_SOURCES_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.external_ol_bridge_sources"
OL_BRIDGE_EVENTS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.external_ol_bridge_events"


def _ensure_bridge_tables() -> None:
    """Lazily create OL bridge tables."""
    try:
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {OL_BRIDGE_SOURCES_TABLE} (
            source_id STRING, platform STRING, name STRING, description STRING,
            receive_token STRING, active BOOLEAN,
            created_at TIMESTAMP, last_push_at TIMESTAMP, total_events LONG
        ) USING DELTA""")
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {OL_BRIDGE_EVENTS_TABLE} (
            event_id STRING, source_id STRING, platform STRING,
            job_namespace STRING, job_name STRING, run_id STRING,
            input_datasets STRING, output_datasets STRING,
            event_type STRING, event_time TIMESTAMP, received_at TIMESTAMP
        ) USING DELTA""")
    except Exception as e:
        logger.warning(f"ol_bridge: could not ensure tables: {e}")


_bridge_tables_ensured = False


def _lazy_ensure_bridge():
    global _bridge_tables_ensured
    if not _bridge_tables_ensured:
        _ensure_bridge_tables()
        _bridge_tables_ensured = True


class OLBridgeSourceIn(BaseModel):
    platform: str  # snowflake | bigquery | spark | flink | dbt_cloud | airflow | custom
    name: str       # friendly label, e.g. "Snowflake PROD"
    description: Optional[str] = ""


@router.post("/ol-bridge/register")
async def register_ol_bridge_source(request: Request, body: OLBridgeSourceIn):
    """Register an external OL-emitting platform as a lineage bridge source.

    Supports any platform with an OpenLineage transport: Snowflake Horizon,
    BigQuery (via OL proxy), Spark + openlineage-spark, Flink, dbt Cloud, etc.

    Returns a source_id and the receive URL to configure on the external system.
    The source_id acts as the authentication token — keep it secret.

    A2/A15 FIX: Admin-gated — registration creates a trust anchor. Non-admins
    should not be able to create endpoints that inject lineage events.
    """
    # A2/A15 FIX: Require admin for OL bridge registration
    from backend.main import _get_user_info
    _, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to register OL bridge sources")
    _lazy_ensure_bridge()
    source_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    platform_safe = _validate_platform(body.platform)
    name_safe = sql_str(body.name, 300)
    desc_safe = sql_str(body.description or "", 1000)
    try:
        _execute_sql(f"""
            INSERT INTO {OL_BRIDGE_SOURCES_TABLE}
            (source_id, platform, name, description, receive_token, active,
             created_at, last_push_at, total_events)
            VALUES ('{source_id}', '{platform_safe}', '{name_safe}', '{desc_safe}',
                    '{source_id}', true, TIMESTAMP '{now}', NULL, 0)
        """)
        base_url = str(request.base_url).rstrip("/")
        receive_url = f"{base_url}/api/external/ol-bridge/ingest/{source_id}"
        return {
            "status": "ok",
            "source_id": source_id,
            "platform": body.platform,
            "receive_url": receive_url,
            "instructions": (
                f"Point {body.platform} OpenLineage transport to: POST {receive_url}. "
                "Send a JSON body of the form {{\"events\": [<OL RunEvent>, ...]}} or a "
                "single RunEvent object. The source_id in the path authenticates each push."
            ),
        }
    except Exception as e:
        logger.error(f"ol bridge registration failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to register OL bridge source")


@router.post("/ol-bridge/ingest/{source_id}")
async def ingest_ol_bridge_events(request: Request, source_id: str, body: dict):
    """Receive OpenLineage RunEvents from an externally registered platform.

    External systems (Snowflake Horizon, BigQuery, Spark, Flink, dbt Cloud,
    Airflow 2.7+, etc.) POST their native OpenLineage events here.

    Accepts:
      { "events": [<RunEvent>, ...] }  — batch (up to 200 per request)
      <RunEvent>                        — single event (auto-wrapped)

    The source_id path parameter authenticates the push. Events are stored and
    surfaced through GET /api/external/ol-bridge/events for graph integration.
    """
    _lazy_ensure_bridge()
    # A15 FIX: Validate source_id is a proper UUID format (not arbitrary string)
    import re
    _UUID_RE = re.compile(r"^[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}$")
    if not _UUID_RE.match(source_id):
        raise HTTPException(status_code=400, detail="Invalid source_id format")
    safe_id = sql_str(source_id, 100)

    # Validate source exists and is active
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"SELECT platform, name FROM {OL_BRIDGE_SOURCES_TABLE} "
            f"WHERE source_id = '{safe_id}' AND active = true LIMIT 1",
        )
    except Exception as e:
        logger.error(f"ol bridge source lookup failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to validate bridge source")

    if not rows:
        # A15 FIX: Do NOT reveal whether the UUID exists — prevent enumeration
        raise HTTPException(status_code=403, detail="Authentication failed")

    platform = rows[0]["platform"]
    # Re-escaped rather than allow-listed: this is a value read back out of the
    # table, and rows predating the write-side allow-list may hold anything.
    platform_safe = sql_str(platform, 100)

    # Normalize to event list
    if "events" in body:
        events = body["events"]
    elif "eventType" in body:
        events = [body]
    else:
        events = body.get("events", [])

    if not events:
        return {"status": "ok", "ingested": 0}

    now = datetime.now(timezone.utc).isoformat()
    ingested = 0

    try:
        for event in events[:200]:
            eid = str(uuid.uuid4())
            job = event.get("job", {})
            run = event.get("run", {})
            ns = sql_str(job.get("namespace", ""), 500)
            jname = sql_str(job.get("name", ""), 500)
            run_id_val = sql_str(run.get("runId", ""), 200)
            evt_type = sql_str(event.get("eventType", "COMPLETE"), 50)
            evt_time = sql_str(event.get("eventTime", now), 50)
            inputs_json = sql_str(json.dumps(event.get("inputs", [])), 4000)
            outputs_json = sql_str(json.dumps(event.get("outputs", [])), 4000)

            _execute_sql(f"""
                INSERT INTO {OL_BRIDGE_EVENTS_TABLE}
                (event_id, source_id, platform, job_namespace, job_name, run_id,
                 input_datasets, output_datasets, event_type, event_time, received_at)
                VALUES ('{eid}', '{safe_id}', '{platform_safe}',
                        '{ns}', '{jname}', '{run_id_val}',
                        '{inputs_json}', '{outputs_json}',
                        '{evt_type}', TIMESTAMP '{evt_time}', TIMESTAMP '{now}')
            """)
            ingested += 1

        # Update push stats on source record
        await asyncio.to_thread(
            _execute_sql,
            f"""UPDATE {OL_BRIDGE_SOURCES_TABLE}
                SET last_push_at = TIMESTAMP '{now}',
                    total_events = COALESCE(total_events, 0) + {ingested}
                WHERE source_id = '{safe_id}'""",
        )

        return {"status": "ok", "ingested": ingested, "source_id": source_id, "platform": platform}
    except Exception as e:
        logger.error(f"ol bridge ingest failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to ingest OL events")


@router.get("/ol-bridge/sources")
async def list_ol_bridge_sources(request: Request):
    """List all registered OL bridge sources with push statistics.

    Shows which external platforms are configured, when they last pushed,
    and the total number of events received from each.

    Admin-gated: the response includes source_id, which doubles as the ingest
    credential (see the ingest handler) — listing it to every user hands out the
    token that lets a caller inject lineage events.
    """
    require_admin(request)
    _lazy_ensure_bridge()
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"""SELECT source_id, platform, name, description, active,
                       created_at, last_push_at, total_events
                FROM {OL_BRIDGE_SOURCES_TABLE}
                ORDER BY platform, name""",
        )
        return {"sources": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"ol bridge source list failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to list OL bridge sources")


@router.get("/ol-bridge/events")
async def get_ol_bridge_events(
    request: Request,
    source_id: Optional[str] = Query(None),
    platform: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
):
    """Inspect OL events received via the bridge.

    Filterable by source_id or platform. Returns job names, namespaces,
    input/output datasets, and timestamps — enabling operators to verify
    that external platform lineage is flowing correctly before graph integration.
    """
    _lazy_ensure_bridge()
    conditions = ["1=1"]
    if source_id:
        conditions.append(f"source_id = '{sql_str(source_id, 100)}'")
    if platform:
        conditions.append(f"platform = '{_validate_platform(platform)}'")
    where = " AND ".join(conditions)
    try:
        rows = await asyncio.to_thread(
            _execute_sql,
            f"""SELECT event_id, source_id, platform, job_namespace, job_name,
                       event_type, event_time, received_at
                FROM {OL_BRIDGE_EVENTS_TABLE}
                WHERE {where}
                ORDER BY received_at DESC
                LIMIT {limit}""",
        )
        return {"events": rows, "count": len(rows)}
    except Exception as e:
        logger.error(f"ol bridge event query failed: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch OL bridge events")
