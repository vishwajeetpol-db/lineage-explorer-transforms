"""Governance & classification — capability 17.

Surfaces ownership, UC tags, and PII/PCI classification rules for every table
and column in a given table. Two data sources:

  1. system.information_schema — authoritative ownership + column metadata.
  2. governance_config (app-owned Delta table) — user-defined PII/PCI rules that
     map column-name patterns or explicit tag names to sensitivity levels.

The /api/governance route also reads system.access.column_lineage to flag
columns that are *derived from* a sensitive source even if they are not
directly tagged themselves (downstream-propagation blast radius).
"""
from __future__ import annotations

import os
import re
import logging
from functools import lru_cache
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
GOV_CONFIG_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.governance_config"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

# Default PII/PCI column-name heuristics applied when no governance_config row
# overrides them.  Keys are sensitivity labels; values are compiled regexes.
_DEFAULT_PII_PATTERNS: dict[str, re.Pattern] = {
    "PII": re.compile(
        r"(\b|_)(name|email|phone|mobile|ssn|dob|birth|address|zip|postal|"
        r"gender|sex|race|ethnicity|passport|license|nhs|national_id|ip_addr|"
        r"lat|lon|latitude|longitude|geo)(\b|_)",
        re.IGNORECASE,
    ),
    "PCI": re.compile(
        r"(\b|_)(card|pan|cvv|ccv|cvc|expiry|exp_date|account_number|iban|sort_code)(\b|_)",
        re.IGNORECASE,
    ),
}


# ---------------------------------------------------------------------------
# Internal SQL helper (mirrors the pattern throughout the backend)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Governance config DDL (auto-created on first write)
# ---------------------------------------------------------------------------

def _ensure_governance_config_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {GOV_CONFIG_TABLE} ("
            f"  rule_id STRING,"
            f"  catalog STRING,"
            f"  schema STRING,"
            f"  table_pattern STRING,"
            f"  column_pattern STRING,"
            f"  tag_name STRING,"
            f"  sensitivity STRING,"
            f"  owner STRING,"
            f"  created_at TIMESTAMP,"
            f"  updated_at TIMESTAMP,"
            f"  notes STRING"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"governance: could not ensure {GOV_CONFIG_TABLE} (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_table_governance(catalog: str, schema: str, table: str) -> dict:
    """Return ownership + column sensitivity classification for `catalog.schema.table`.

    Never raises — degraded fields are omitted rather than erroring the response.
    """
    full_name = f"{catalog}.{schema}.{table}"
    result: dict = {
        "table_full_name": full_name,
        "owner": None,
        "table_type": None,
        "created_by": None,
        "created_at": None,
        "last_altered_by": None,
        "last_altered_at": None,
        "comment": None,
        "tags": [],
        "columns": [],
        "sensitive_columns": [],
        "config_rules_applied": 0,
    }

    # 1. Table-level metadata from information_schema
    try:
        rows = _execute_sql(
            f"SELECT table_owner, table_type, created, created_by, last_altered, last_altered_by, comment "
            f"FROM {catalog}.information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' LIMIT 1"
        )
        if rows:
            result["owner"] = rows[0].get("table_owner")
            result["table_type"] = rows[0].get("table_type")
            result["created_by"] = rows[0].get("created_by")
            result["created_at"] = str(rows[0].get("created") or "") or None
            result["last_altered_by"] = rows[0].get("last_altered_by")
            result["last_altered_at"] = str(rows[0].get("last_altered") or "") or None
            result["comment"] = rows[0].get("comment")
    except Exception as e:
        logger.info(f"governance: could not fetch table metadata for {full_name}: {e}")

    # 2. UC tags via information_schema.column_tags (DBR 14.1+)
    try:
        tag_rows = _execute_sql(
            f"SELECT tag_name, tag_value "
            f"FROM {catalog}.information_schema.column_tags "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
        )
        result["tags"] = [{"name": r["tag_name"], "value": r.get("tag_value")} for r in tag_rows]
    except Exception as e:
        logger.debug(f"governance: column_tags not available for {full_name}: {e}")

    # 3. Column list from information_schema.columns
    try:
        col_rows = _execute_sql(
            f"SELECT column_name, data_type, is_nullable, comment "
            f"FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
            f"ORDER BY ordinal_position"
        )
        result["columns"] = [
            {
                "name": r["column_name"],
                "type": r["data_type"],
                "nullable": r["is_nullable"],
                "comment": r.get("comment"),
                "sensitivity": None,  # filled below
                "sensitivity_source": None,
            }
            for r in col_rows
        ]
    except Exception as e:
        logger.info(f"governance: could not fetch columns for {full_name}: {e}")
        return result

    # 4. Load user-defined governance config rules
    config_rules: list[dict] = []
    try:
        _ensure_governance_config_table()
        safe_cat = catalog.replace("'", "")
        safe_sch = schema.replace("'", "")
        safe_tbl = table.replace("'", "")
        config_rules = _execute_sql(
            f"SELECT column_pattern, tag_name, sensitivity FROM {GOV_CONFIG_TABLE} "
            f"WHERE (catalog IS NULL OR catalog = '{safe_cat}') "
            f"  AND (schema IS NULL OR schema = '{safe_sch}') "
            f"  AND (table_pattern IS NULL OR '{safe_tbl}' LIKE table_pattern)"
        )
    except Exception as e:
        logger.debug(f"governance: could not load config rules: {e}")

    result["config_rules_applied"] = len(config_rules)

    # 5. Classify columns using config rules then default heuristics
    tag_index: dict[str, list[str]] = {}
    for t in result["tags"]:
        tag_index.setdefault(t["name"], []).append(t.get("value") or "")

    sensitive: list[dict] = []
    for col in result["columns"]:
        col_name: str = col["name"]
        sensitivity = None
        source = None

        # 5a. Config rules take precedence (first match wins)
        for rule in config_rules:
            pattern = rule.get("column_pattern") or ""
            tag_name = rule.get("tag_name") or ""
            if pattern and re.search(pattern, col_name, re.IGNORECASE):
                sensitivity = rule.get("sensitivity", "SENSITIVE")
                source = "config_rule"
                break
            if tag_name and tag_name in tag_index:
                sensitivity = rule.get("sensitivity", "SENSITIVE")
                source = "tag_rule"
                break

        # 5b. Default heuristic patterns
        if sensitivity is None:
            for label, pattern in _DEFAULT_PII_PATTERNS.items():
                if pattern.search(col_name):
                    sensitivity = label
                    source = "heuristic"
                    break

        col["sensitivity"] = sensitivity
        col["sensitivity_source"] = source
        if sensitivity:
            sensitive.append({"column": col_name, "sensitivity": sensitivity, "source": source})

    result["sensitive_columns"] = sensitive
    return result


def list_governance_rules() -> list[dict]:
    """Return all user-defined governance config rules."""
    try:
        _ensure_governance_config_table()
        return _execute_sql(f"SELECT * FROM {GOV_CONFIG_TABLE} ORDER BY updated_at DESC")
    except Exception as e:
        logger.info(f"governance: could not list rules: {e}")
        return []


def upsert_governance_rule(rule: dict, actor: str) -> dict:
    """Insert or replace a governance classification rule. `actor` is the user performing the write."""
    _ensure_governance_config_table()
    safe = lambda s: (s or "").replace("'", "")
    rule_id = safe(rule.get("rule_id") or f"rule_{hash(str(rule)) & 0xFFFFFF:06x}")
    _execute_sql(
        f"INSERT OVERWRITE {GOV_CONFIG_TABLE} "
        f"SELECT rule_id, catalog, schema, table_pattern, column_pattern, tag_name, sensitivity, owner, created_at, updated_at, notes "
        f"FROM {GOV_CONFIG_TABLE} WHERE rule_id != '{rule_id}' "
        f"UNION ALL "
        f"SELECT '{rule_id}', '{safe(rule.get('catalog'))}', '{safe(rule.get('schema'))}', "
        f"'{safe(rule.get('table_pattern'))}', '{safe(rule.get('column_pattern'))}', "
        f"'{safe(rule.get('tag_name'))}', '{safe(rule.get('sensitivity', 'SENSITIVE'))}', "
        f"'{safe(actor)}', current_timestamp(), current_timestamp(), '{safe(rule.get('notes'))}'"
    )
    return {"rule_id": rule_id, "status": "upserted"}


def delete_governance_rule(rule_id: str) -> dict:
    """Delete a governance classification rule by id."""
    _ensure_governance_config_table()
    safe_id = (rule_id or "").replace("'", "")
    _execute_sql(f"DELETE FROM {GOV_CONFIG_TABLE} WHERE rule_id = '{safe_id}'")
    return {"rule_id": safe_id, "status": "deleted"}


def get_downstream_sensitivity_propagation(catalog: str, schema: str, table: str) -> list[dict]:
    """Walk system.access.column_lineage to find downstream columns that inherit
    sensitivity from any sensitive column in `catalog.schema.table`.
    Returns a flat list of {source_column, target_table, target_column, sensitivity}.
    """
    gov = get_table_governance(catalog, schema, table)
    sensitive_cols = {c["column"] for c in gov.get("sensitive_columns", [])}
    if not sensitive_cols:
        return []

    full_name = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(
            f"SELECT source_column_name, target_table_full_name, target_column_name "
            f"FROM system.access.column_lineage "
            f"WHERE source_table_full_name = '{full_name}' "
            f"  AND source_column_name IN ({', '.join(repr(c) for c in sensitive_cols)}) "
            f"ORDER BY target_table_full_name, target_column_name "
            f"LIMIT 500"
        )
        return [
            {
                "source_column": r["source_column_name"],
                "target_table": r["target_table_full_name"],
                "target_column": r["target_column_name"],
                "sensitivity": next(
                    (c["sensitivity"] for c in gov["sensitive_columns"] if c["column"] == r["source_column_name"]),
                    "SENSITIVE",
                ),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"governance: downstream sensitivity propagation failed: {e}")
        return []
