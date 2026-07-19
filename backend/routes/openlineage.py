"""Routes for Open Standards Export — capability 17.

Export lineage data in OpenLineage-compatible JSON format for interop
with Marquez, Atlan, DataHub, OpenMetadata, and other catalog systems.

Endpoints:
  GET  /api/export/openlineage        — export graph as OpenLineage events
  GET  /api/export/openlineage/schema  — export as OpenLineage dataset facets
  POST /api/import/openlineage        — ingest OpenLineage events into the graph

OpenLineage spec: https://openlineage.io/spec/2-0-2/OpenLineage.json
"""
from __future__ import annotations

import os
import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client, get_table_lineage, get_schema_column_lineage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["openlineage"])

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
OPENLINEAGE_PRODUCER = "https://github.com/databricks/lineage-explorer"
OPENLINEAGE_SCHEMA_URL = "https://openlineage.io/spec/2-0-2/OpenLineage.json"


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


def _table_to_openlineage_dataset(catalog: str, schema: str, table: str) -> dict:
    """Convert a UC table reference to an OpenLineage Dataset."""
    return {
        "namespace": f"databricks://{catalog}.{schema}",
        "name": table,
        "facets": {
            "dataSource": {
                "_producer": OPENLINEAGE_PRODUCER,
                "_schemaURL": OPENLINEAGE_SCHEMA_URL + "#/$defs/DataSourceDatasetFacet",
                "name": f"databricks://{catalog}",
                "uri": f"databricks://{catalog}.{schema}.{table}",
            }
        },
    }


def _build_openlineage_run_event(
    source_tables: list[dict], target_table: dict, entity_type: str, entity_id: str, event_time: str
) -> dict:
    """Build a single OpenLineage RunEvent."""
    return {
        "eventType": "COMPLETE",
        "eventTime": event_time,
        "producer": OPENLINEAGE_PRODUCER,
        "schemaURL": OPENLINEAGE_SCHEMA_URL,
        "run": {
            "runId": f"{entity_type}-{entity_id}",
            "facets": {},
        },
        "job": {
            "namespace": "databricks",
            "name": f"{entity_type}/{entity_id}",
            "facets": {
                "jobType": {
                    "_producer": OPENLINEAGE_PRODUCER,
                    "_schemaURL": OPENLINEAGE_SCHEMA_URL + "#/$defs/JobTypeJobFacet",
                    "processingType": "BATCH",
                    "integration": "DATABRICKS",
                    "jobType": entity_type.upper(),
                }
            },
        },
        "inputs": source_tables,
        "outputs": [target_table],
    }


@router.get("/api/export/openlineage")
async def export_openlineage(
    request: Request,
    catalog: str = Query(...),
    schema: Optional[str] = Query(None),
    include_columns: bool = Query(False),
):
    """Export lineage graph as OpenLineage RunEvents JSON.

    Each table-to-table edge (via a producing entity) becomes one RunEvent
    with inputs/outputs mapped to OpenLineage Datasets.
    """
    try:
        lineage = await asyncio.to_thread(get_table_lineage, catalog, schema, False)

        # Build a lookup of node IDs to table info
        table_nodes = {}
        entity_nodes = {}
        for node in lineage.nodes:
            if getattr(node, "node_type", None) == "table":
                table_nodes[node.id] = node
            elif getattr(node, "node_type", None) == "entity":
                entity_nodes[node.id] = node

        # Convert edges to OpenLineage events
        events = []
        # Group edges by target: find source→entity→target patterns
        for edge in lineage.edges:
            src_node = table_nodes.get(edge.source) or entity_nodes.get(edge.source)
            tgt_node = table_nodes.get(edge.target) or entity_nodes.get(edge.target)

            # Only emit events for table→entity or entity→table edges
            if src_node and tgt_node:
                if getattr(src_node, "node_type", None) == "table" and getattr(tgt_node, "node_type", None) == "entity":
                    # Source table feeding into an entity - collect
                    pass
                elif getattr(src_node, "node_type", None) == "entity" and getattr(tgt_node, "node_type", None) == "table":
                    # Entity producing a target table
                    parts = tgt_node.id.replace("table:", "").split(".")
                    if len(parts) == 3:
                        output_ds = _table_to_openlineage_dataset(parts[0], parts[1], parts[2])
                        # Find all inputs for this entity
                        input_datasets = []
                        for e2 in lineage.edges:
                            if e2.target == src_node.id:
                                input_node = table_nodes.get(e2.source)
                                if input_node:
                                    inp_parts = input_node.id.replace("table:", "").split(".")
                                    if len(inp_parts) == 3:
                                        input_datasets.append(
                                            _table_to_openlineage_dataset(inp_parts[0], inp_parts[1], inp_parts[2])
                                        )

                        event = _build_openlineage_run_event(
                            source_tables=input_datasets,
                            target_table=output_ds,
                            entity_type=getattr(src_node, "entity_type", "JOB"),
                            entity_id=getattr(src_node, "entity_id", "unknown"),
                            event_time=datetime.now(timezone.utc).isoformat(),
                        )
                        events.append(event)

        # Optionally add column-level facets
        if include_columns and schema:
            try:
                col_lineage = await asyncio.to_thread(get_schema_column_lineage, catalog, schema, False)
                # Add SchemaDatasetFacet to outputs where we have column info
                for event in events:
                    for output in event.get("outputs", []):
                        table_name = output.get("name", "")
                        col_fields = []
                        if hasattr(col_lineage, "edges"):
                            for ce in col_lineage.edges:
                                if table_name in str(getattr(ce, "target_table", "")):
                                    col_fields.append({
                                        "name": getattr(ce, "target_column", "unknown"),
                                        "type": "STRING",
                                    })
                        if col_fields:
                            output["facets"]["schema"] = {
                                "_producer": OPENLINEAGE_PRODUCER,
                                "_schemaURL": OPENLINEAGE_SCHEMA_URL + "#/$defs/SchemaDatasetFacet",
                                "fields": col_fields,
                            }
            except Exception as e:
                logger.debug(f"Column facets unavailable: {e}")

        return JSONResponse(
            content={"events": events, "count": len(events), "schemaURL": OPENLINEAGE_SCHEMA_URL},
            headers={"Content-Type": "application/json"},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/import/openlineage")
async def import_openlineage(request: Request, body: dict):
    """Ingest OpenLineage RunEvents into the lineage graph.

    Accepts a list of RunEvent objects and registers them as external
    lineage edges in an app-owned table."""
    events = body.get("events", [])
    if not events:
        raise HTTPException(status_code=400, detail="No events provided")

    LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
    LINEAGE_SCHEMA_NAME = os.environ.get("LINEAGE_SCHEMA", "lineage")
    EXT_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA_NAME}.external_lineage_events"

    imported = 0
    try:
        # Ensure the external events table exists
        _execute_sql(f"""CREATE TABLE IF NOT EXISTS {EXT_TABLE} (
            event_id STRING, event_type STRING, event_time TIMESTAMP,
            job_namespace STRING, job_name STRING, run_id STRING,
            input_datasets STRING, output_datasets STRING,
            raw_event STRING, imported_at TIMESTAMP
        ) USING DELTA""")

        now = datetime.now(timezone.utc).isoformat()
        for event in events[:100]:  # Cap at 100 per request
            import uuid
            eid = str(uuid.uuid4())
            job = event.get("job", {})
            run = event.get("run", {})
            inputs_json = json.dumps(event.get("inputs", [])).replace("'", "''")
            outputs_json = json.dumps(event.get("outputs", [])).replace("'", "''")
            raw_json = json.dumps(event).replace("'", "''")[:8000]

            _execute_sql(f"""
                INSERT INTO {EXT_TABLE}
                (event_id, event_type, event_time, job_namespace, job_name, run_id, input_datasets, output_datasets, raw_event, imported_at)
                VALUES ('{eid}', '{event.get("eventType", "COMPLETE")}',
                        TIMESTAMP '{event.get("eventTime", now)}',
                        '{job.get("namespace", "").replace(chr(39), chr(39)*2)[:500]}',
                        '{job.get("name", "").replace(chr(39), chr(39)*2)[:500]}',
                        '{run.get("runId", "").replace(chr(39), chr(39)*2)[:200]}',
                        '{inputs_json[:4000]}', '{outputs_json[:4000]}',
                        '{raw_json}', TIMESTAMP '{now}')
            """)
            imported += 1

        return {"status": "ok", "imported": imported}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
