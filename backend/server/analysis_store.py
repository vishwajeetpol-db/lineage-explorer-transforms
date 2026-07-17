"""Versioned analysis store — capability 28.

Caches and versions LLM analysis results (capability 27 / Approach A)
in the `producer_analysis` Delta table inside the app-owned lineage
schema. Keyed by (entity_type, entity_id, source_hash) so repeated
calls don't re-run the LLM, and old versions are retained for audit.

Table DDL (auto-created on first write):
    producer_analysis (
        entity_type     STRING,   -- JOB | PIPELINE | NOTEBOOK | QUERY
        entity_id       STRING,   -- opaque entity identifier
        source_hash     STRING,   -- SHA-256 of the source code (8-hex prefix)
        target_table    STRING,   -- full table name the code writes to
        analysis_json   STRING,   -- JSON-encoded list[dict] from llm.analyze
        llm_model       STRING,   -- model name used
        analyzed_at     TIMESTAMP,
        analyzed_by     STRING,   -- user who triggered the analysis
        version         LONG      -- monotonically increasing per (entity_type, entity_id)
    )

Lookup is latest-version-first within a (entity_type, entity_id) group.
Source-hash deduplication prevents re-calling the LLM when source code
hasn't changed since the last analysis.
"""
from __future__ import annotations

import os
import json
import hashlib
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
ANALYSIS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.producer_analysis"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")


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


def _ensure_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {ANALYSIS_TABLE} ("
            f"  entity_type STRING,"
            f"  entity_id STRING,"
            f"  source_hash STRING,"
            f"  target_table STRING,"
            f"  analysis_json STRING,"
            f"  llm_model STRING,"
            f"  analyzed_at TIMESTAMP,"
            f"  analyzed_by STRING,"
            f"  version LONG"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"analysis_store: could not ensure {ANALYSIS_TABLE}: {e}")


def _source_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8", errors="replace")).hexdigest()[:16]


def get_cached_analysis(
    entity_type: str,
    entity_id: str,
    source_code: str,
) -> Optional[list[dict]]:
    """Return cached analysis for this entity if source_code hasn't changed.

    Returns None if no cache row exists or the source hash differs
    (indicating the source code changed since last analysis).
    """
    h = _source_hash(source_code)
    safe_et = entity_type.replace("'", "")
    safe_id = entity_id.replace("'", "")
    try:
        _ensure_table()
        rows = _execute_sql(
            f"SELECT analysis_json FROM {ANALYSIS_TABLE} "
            f"WHERE entity_type = '{safe_et}' AND entity_id = '{safe_id}' "
            f"  AND source_hash = '{h}' "
            f"ORDER BY version DESC LIMIT 1"
        )
        if rows and rows[0].get("analysis_json"):
            return json.loads(rows[0]["analysis_json"])
    except Exception as e:
        logger.debug(f"analysis_store: cache miss for {entity_type}/{entity_id}: {e}")
    return None


def save_analysis(
    entity_type: str,
    entity_id: str,
    source_code: str,
    target_table: str,
    analysis: list[dict],
    llm_model: str,
    actor: str,
) -> None:
    """Persist a new analysis row (append-only; old versions are retained)."""
    h = _source_hash(source_code)
    safe = lambda s: (s or "").replace("'", "").replace("\\", "")
    # Compute next version number
    try:
        _ensure_table()
        ver_rows = _execute_sql(
            f"SELECT COALESCE(MAX(version), 0) AS max_ver FROM {ANALYSIS_TABLE} "
            f"WHERE entity_type = '{safe(entity_type)}' AND entity_id = '{safe(entity_id)}'"
        )
        next_ver = int((ver_rows[0].get("max_ver") or 0)) + 1 if ver_rows else 1
    except Exception:
        next_ver = 1
    try:
        analysis_json = json.dumps(analysis)
        _execute_sql(
            f"INSERT INTO {ANALYSIS_TABLE} VALUES ("
            f"'{safe(entity_type)}', '{safe(entity_id)}', '{h}', "
            f"'{safe(target_table)}', "
            f"'{analysis_json.replace(chr(39), chr(39) + chr(39))}', "
            f"'{safe(llm_model)}', current_timestamp(), '{safe(actor)}', {next_ver})"
        )
    except Exception as e:
        logger.warning(f"analysis_store: could not save analysis for {entity_type}/{entity_id}: {e}")


def list_analyses(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return analysis history rows (metadata only, no analysis_json). Admin-facing."""
    filters = []
    if entity_type:
        filters.append(f"entity_type = '{entity_type.replace(chr(39), '')}' ")
    if entity_id:
        filters.append(f"entity_id = '{entity_id.replace(chr(39), '')}' ")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    try:
        _ensure_table()
        return _execute_sql(
            f"SELECT entity_type, entity_id, source_hash, target_table, "
            f"       llm_model, analyzed_at, analyzed_by, version "
            f"FROM {ANALYSIS_TABLE} {where} "
            f"ORDER BY analyzed_at DESC LIMIT {limit}"
        )
    except Exception as e:
        logger.info(f"analysis_store: list_analyses failed: {e}")
        return []
