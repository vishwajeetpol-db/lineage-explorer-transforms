"""Versioned analysis store — capability 28.

Caches and versions LLM analysis results (capability 27 / Approach A) in the
`producer_analysis` Delta table inside the app-owned lineage schema.

Keyed by (entity_type, entity_id); each analysis appends a new monotonically
increasing `version`. The full source-code snapshot is stored per version so
two versions can be diffed to see exactly what changed in the producer's code.
`source_hash` (SHA-256 of the source) drives stale-detection: if the producer's
current source hashes differently than the latest stored version, the UI's
re-analyze button lights up.

Table DDL (auto-created / auto-migrated on first write):
    producer_analysis (
        entity_type     STRING,   -- JOB | PIPELINE | NOTEBOOK | QUERY
        entity_id       STRING,   -- opaque entity identifier
        source_hash     STRING,   -- SHA-256 of the source code (16-hex prefix)
        source_code     STRING,   -- full source snapshot for this version (for diff)
        target_table    STRING,   -- full table name the code writes to
        analysis_json   STRING,   -- JSON-encoded list[dict] from llm.analyze
        llm_model       STRING,   -- model name used
        analyzed_at     TIMESTAMP,
        analyzed_by     STRING,   -- user who triggered the analysis
        version         LONG      -- monotonically increasing per (entity_type, entity_id)
    )

The table name is configurable via PRODUCER_ANALYSIS_TABLE (else it derives from
LINEAGE_CATALOG/LINEAGE_SCHEMA).
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
# Fully configurable table name; falls back to the app-owned lineage schema.
ANALYSIS_TABLE = os.environ.get(
    "PRODUCER_ANALYSIS_TABLE",
    f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.producer_analysis",
)
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

_table_ready = False


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
    """Create the table if missing, and migrate older schemas that predate the
    source_code column (ADD COLUMN is a no-op if it already exists)."""
    global _table_ready
    if _table_ready:
        return
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {ANALYSIS_TABLE} ("
            f"  entity_type STRING,"
            f"  entity_id STRING,"
            f"  source_hash STRING,"
            f"  source_code STRING,"
            f"  target_table STRING,"
            f"  analysis_json STRING,"
            f"  llm_model STRING,"
            f"  analyzed_at TIMESTAMP,"
            f"  analyzed_by STRING,"
            f"  version LONG"
            f") USING DELTA"
        )
        # Migrate pre-existing tables that lack source_code.
        try:
            _execute_sql(f"ALTER TABLE {ANALYSIS_TABLE} ADD COLUMNS (source_code STRING)")
        except Exception:
            pass  # already present
        _table_ready = True
    except Exception as e:
        logger.warning(f"analysis_store: could not ensure {ANALYSIS_TABLE}: {e}")


def _source_hash(source_code: str) -> str:
    return hashlib.sha256(source_code.encode("utf-8", errors="replace")).hexdigest()[:16]


def _sql_str(s: str) -> str:
    """Escape a value for a single-quoted SQL literal (quotes + backslashes)."""
    return (s or "").replace("\\", "\\\\").replace("'", "''")


def _key_where(entity_type: str, entity_id: str, target_table: str) -> str:
    """WHERE clause keying a version to (entity_type, entity_id, target_table).

    A single producer (pipeline/job) often writes MANY tables, so the target
    table MUST be part of the key — otherwise loading a producer for table B
    would surface the stored analysis for table A.
    """
    return (
        f"entity_type = '{_sql_str(entity_type)}' "
        f"AND entity_id = '{_sql_str(entity_id)}' "
        f"AND target_table = '{_sql_str(target_table)}'"
    )


def get_cached_analysis(
    entity_type: str,
    entity_id: str,
    target_table: str,
    source_code: str,
) -> Optional[list[dict]]:
    """Return cached analysis for this (entity, target table) if source unchanged."""
    h = _source_hash(source_code)
    try:
        _ensure_table()
        rows = _execute_sql(
            f"SELECT analysis_json FROM {ANALYSIS_TABLE} "
            f"WHERE {_key_where(entity_type, entity_id, target_table)} "
            f"  AND source_hash = '{h}' "
            f"ORDER BY version DESC LIMIT 1"
        )
        if rows and rows[0].get("analysis_json"):
            return json.loads(rows[0]["analysis_json"])
    except Exception as e:
        logger.debug(f"analysis_store: cache miss for {entity_type}/{entity_id}/{target_table}: {e}")
    return None


def _decode_row(r: dict) -> dict:
    """Parse a raw DB row into an analysis dict with columns decoded."""
    try:
        columns = json.loads(r.get("analysis_json") or "[]")
    except Exception:
        columns = []
    return {
        "entity_type": r.get("entity_type"),
        "entity_id": r.get("entity_id"),
        "source_hash": r.get("source_hash"),
        "source_code": r.get("source_code"),
        "target_table": r.get("target_table"),
        "columns": columns,
        "llm_model": r.get("llm_model"),
        "analyzed_at": str(r.get("analyzed_at") or ""),
        "analyzed_by": r.get("analyzed_by"),
        "version": int(r.get("version") or 0),
    }


_FULL_COLS = (
    "entity_type, entity_id, source_hash, source_code, target_table, "
    "analysis_json, llm_model, analyzed_at, analyzed_by, version"
)


def get_latest_version(entity_type: str, entity_id: str, target_table: str) -> Optional[dict]:
    """Return the latest stored version for (entity, target table). None if never stored."""
    try:
        _ensure_table()
        rows = _execute_sql(
            f"SELECT {_FULL_COLS} FROM {ANALYSIS_TABLE} "
            f"WHERE {_key_where(entity_type, entity_id, target_table)} "
            f"ORDER BY version DESC LIMIT 1"
        )
        if rows:
            return _decode_row(rows[0])
    except Exception as e:
        logger.info(f"analysis_store: get_latest_version failed for {entity_type}/{entity_id}/{target_table}: {e}")
    return None


def get_version(entity_type: str, entity_id: str, target_table: str, version: int) -> Optional[dict]:
    """Return a specific stored version (full row incl. source snapshot)."""
    try:
        _ensure_table()
        rows = _execute_sql(
            f"SELECT {_FULL_COLS} FROM {ANALYSIS_TABLE} "
            f"WHERE {_key_where(entity_type, entity_id, target_table)} "
            f"  AND version = {int(version)} LIMIT 1"
        )
        if rows:
            return _decode_row(rows[0])
    except Exception as e:
        logger.info(f"analysis_store: get_version failed for {entity_type}/{entity_id}/{target_table} v{version}: {e}")
    return None


def save_analysis(
    entity_type: str,
    entity_id: str,
    source_code: str,
    target_table: str,
    analysis: list[dict],
    llm_model: str,
    actor: str,
) -> int:
    """Persist a new analysis version (append-only). Returns the new version number.

    Version is monotonic per (entity_type, entity_id, target_table)."""
    h = _source_hash(source_code)
    next_ver = 1
    try:
        _ensure_table()
        ver_rows = _execute_sql(
            f"SELECT COALESCE(MAX(version), 0) AS max_ver FROM {ANALYSIS_TABLE} "
            f"WHERE {_key_where(entity_type, entity_id, target_table)}"
        )
        next_ver = int((ver_rows[0].get("max_ver") or 0)) + 1 if ver_rows else 1
    except Exception:
        next_ver = 1
    try:
        analysis_json = json.dumps(analysis)
        _execute_sql(
            f"INSERT INTO {ANALYSIS_TABLE} "
            f"(entity_type, entity_id, source_hash, source_code, target_table, "
            f" analysis_json, llm_model, analyzed_at, analyzed_by, version) VALUES ("
            f"'{_sql_str(entity_type)}', '{_sql_str(entity_id)}', '{h}', "
            f"'{_sql_str(source_code)}', '{_sql_str(target_table)}', "
            f"'{_sql_str(analysis_json)}', '{_sql_str(llm_model)}', "
            f"current_timestamp(), '{_sql_str(actor)}', {next_ver})"
        )
    except Exception as e:
        logger.warning(f"analysis_store: could not save analysis for {entity_type}/{entity_id}: {e}")
    return next_ver


def list_versions(entity_type: str, entity_id: str, target_table: str, limit: int = 100) -> list[dict]:
    """Return version metadata (no payload) newest-first for one (entity, table)."""
    try:
        _ensure_table()
        return _execute_sql(
            f"SELECT version, llm_model, source_hash, analyzed_at, analyzed_by, target_table "
            f"FROM {ANALYSIS_TABLE} "
            f"WHERE {_key_where(entity_type, entity_id, target_table)} "
            f"ORDER BY version DESC LIMIT {int(limit)}"
        )
    except Exception as e:
        logger.info(f"analysis_store: list_versions failed: {e}")
        return []


def list_analyses(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Return analysis history rows (metadata only, no payload). Admin-facing."""
    filters = []
    if entity_type:
        filters.append(f"entity_type = '{_sql_str(entity_type)}' ")
    if entity_id:
        filters.append(f"entity_id = '{_sql_str(entity_id)}' ")
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
