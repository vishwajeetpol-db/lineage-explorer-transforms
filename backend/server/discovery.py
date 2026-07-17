"""Search & discovery — capability 22.

Three functions:

  search_assets(q)           — full-text search across UC tables, schemas,
                               catalogs, and the entity log (jobs/pipelines).
  find_sensitive_tables()    — list tables that contain at least one column
                               matching a PII/PCI heuristic or governance rule.
  find_orphan_tables()       — tables with no upstream AND no downstream
                               lineage events within the lookback window.

All are non-fatal and degrade gracefully when system tables are inaccessible.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.server.governance import _DEFAULT_PII_PATTERNS

logger = logging.getLogger(__name__)

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
LINEAGE_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))


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
# Global asset search
# ---------------------------------------------------------------------------

def search_assets(
    query: str,
    catalogs: Optional[list[str]] = None,
    asset_types: Optional[list[str]] = None,
    limit: int = 50,
) -> list[dict]:
    """Search UC tables by name or comment, returning lightweight asset stubs.

    `asset_types` may contain 'TABLE', 'VIEW', 'EXTERNAL' — default is all.
    Searches across all accessible catalogs unless `catalogs` is specified.
    """
    safe_q = query.strip().replace("'", "").replace("%", "\\%")
    cat_filter = ""
    if catalogs:
        quoted = ", ".join(f"'{c.replace(chr(39), '')}' " for c in catalogs)
        cat_filter = f"AND table_catalog IN ({quoted})"
    type_filter = ""
    if asset_types:
        quoted = ", ".join(f"'{t.upper().replace(chr(39), '')}' " for t in asset_types)
        type_filter = f"AND table_type IN ({quoted})"
    try:
        rows = _execute_sql(
            f"SELECT table_catalog, table_schema, table_name, table_type, "
            f"       table_owner, comment, created "
            f"FROM system.information_schema.tables "
            f"WHERE (LOWER(table_name) LIKE LOWER('%{safe_q}%') "
            f"       OR LOWER(comment) LIKE LOWER('%{safe_q}%')) "
            f"{cat_filter} {type_filter} "
            f"ORDER BY table_catalog, table_schema, table_name "
            f"LIMIT {limit}"
        )
        return [
            {
                "full_name": f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}",
                "catalog": r["table_catalog"],
                "schema": r["table_schema"],
                "name": r["table_name"],
                "type": r.get("table_type"),
                "owner": r.get("table_owner"),
                "comment": r.get("comment"),
                "created_at": str(r.get("created") or ""),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"discovery: search_assets failed for query '{query}': {e}")
        return []


# ---------------------------------------------------------------------------
# PII / sensitive finder
# ---------------------------------------------------------------------------

def find_sensitive_tables(catalog: Optional[str] = None, schema: Optional[str] = None) -> list[dict]:
    """Return tables that contain at least one PII/PCI-heuristic column.

    Queries information_schema.columns and applies the same column-name
    heuristics as governance.py.  For each matching table, returns the
    list of sensitive columns found.
    """
    cat_filter = f"AND table_catalog = '{catalog}'" if catalog else ""
    sch_filter = f"AND table_schema = '{schema}'" if schema else ""
    try:
        col_rows = _execute_sql(
            f"SELECT table_catalog, table_schema, table_name, column_name "
            f"FROM system.information_schema.columns "
            f"WHERE table_schema NOT IN ('information_schema') "
            f"{cat_filter} {sch_filter} "
            f"ORDER BY table_catalog, table_schema, table_name, ordinal_position"
        )
    except Exception as e:
        logger.info(f"discovery: find_sensitive_tables failed: {e}")
        return []

    # Group by table then check each column against heuristics
    tables: dict[str, dict] = {}
    for row in col_rows:
        col_name = row["column_name"]
        full_name = f"{row['table_catalog']}.{row['table_schema']}.{row['table_name']}"
        for label, pattern in _DEFAULT_PII_PATTERNS.items():
            if pattern.search(col_name):
                if full_name not in tables:
                    tables[full_name] = {
                        "full_name": full_name,
                        "catalog": row["table_catalog"],
                        "schema": row["table_schema"],
                        "name": row["table_name"],
                        "sensitive_columns": [],
                    }
                tables[full_name]["sensitive_columns"].append(
                    {"column": col_name, "sensitivity": label}
                )
                break  # one label per column is enough

    return sorted(tables.values(), key=lambda t: t["full_name"])


# ---------------------------------------------------------------------------
# Orphan-table detection
# ---------------------------------------------------------------------------

def find_orphan_tables(catalog: Optional[str] = None, schema: Optional[str] = None) -> list[dict]:
    """Return tables that appear in information_schema but have no lineage events
    (neither upstream source nor downstream consumer) in the lookback window.

    An "orphan" is a table no pipeline reads and no pipeline writes to —
    useful for spotting stale/abandoned assets.
    """
    cat_filter = f"AND t.table_catalog = '{catalog}'" if catalog else ""
    sch_filter = f"AND t.table_schema = '{schema}'" if schema else ""
    try:
        rows = _execute_sql(
            f"SELECT t.table_catalog, t.table_schema, t.table_name, t.table_owner "
            f"FROM system.information_schema.tables t "
            f"WHERE t.table_schema NOT IN ('information_schema') "
            f"{cat_filter} {sch_filter} "
            f"  AND CONCAT(t.table_catalog,'.',t.table_schema,'.',t.table_name) NOT IN ("
            f"    SELECT DISTINCT target_table_full_name "
            f"    FROM system.access.table_lineage "
            f"    WHERE event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp())"
            f"  ) "
            f"  AND CONCAT(t.table_catalog,'.',t.table_schema,'.',t.table_name) NOT IN ("
            f"    SELECT DISTINCT source_table_full_name "
            f"    FROM system.access.table_lineage "
            f"    WHERE event_time >= dateadd(DAY, -{LINEAGE_LOOKBACK_DAYS}, current_timestamp())"
            f"  ) "
            f"ORDER BY t.table_catalog, t.table_schema, t.table_name "
            f"LIMIT 500"
        )
        return [
            {
                "full_name": f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}",
                "catalog": r["table_catalog"],
                "schema": r["table_schema"],
                "name": r["table_name"],
                "owner": r.get("table_owner"),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"discovery: find_orphan_tables failed: {e}")
        return []
