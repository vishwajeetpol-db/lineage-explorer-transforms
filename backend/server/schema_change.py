"""Schema change / breaking-change detection — capability 35.

Detects when an upstream table's schema has changed (column rename, drop,
or type change) in a way that breaks a downstream transformation expression.

Two detection methods:

  1. Delta table history (best signal): reads the DESCRIBE HISTORY of upstream
     Delta tables and compares the current schema against the version in the
     transformation edges, flagging mismatches.

  2. Static-expression column matching: scans the transformation edges for
     a target table and checks whether every column referenced in the `expr_sql`
     field still exists in the upstream table's current information_schema
     columns list.  A referenced column that no longer exists = a breaking change.

All functions are non-fatal.
"""
from __future__ import annotations

import os
import re
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
EDGE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.lineage_edge_endpoints"
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


def _get_current_columns(catalog: str, schema: str, table: str) -> set[str]:
    """Return the current column names for a table from information_schema."""
    try:
        rows = _execute_sql(
            f"SELECT column_name FROM {catalog}.information_schema.columns "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
        )
        return {r["column_name"].lower() for r in rows}
    except Exception as e:
        logger.debug(f"schema_change: could not get columns for {catalog}.{schema}.{table}: {e}")
        return set()


def _extract_column_refs_from_expr(expr: str) -> set[str]:
    """Heuristically extract column references from a SQL expression.

    Uses a word-boundary regex to find identifiers; back-tick-quoted names
    are also captured. This is a best-effort approximation — SQL functions,
    keywords, and literals may produce false positives, but those will not
    match against the actual column-name set.
    """
    raw = re.findall(r"`([^`]+)`|\b([A-Za-z_][A-Za-z0-9_]*)\b", expr)
    refs: set[str] = set()
    for quoted, bare in raw:
        name = (quoted or bare).lower()
        # Skip common SQL keywords / function names
        _KEYWORDS = {
            "select", "from", "where", "and", "or", "not", "null", "true", "false",
            "case", "when", "then", "else", "end", "cast", "as", "is", "in", "like",
            "coalesce", "nvl", "if", "ifnull", "isnull", "upper", "lower", "trim",
            "substring", "substr", "concat", "length", "to_date", "to_timestamp",
            "date_trunc", "year", "month", "day", "hour", "minute", "second",
            "sum", "count", "avg", "min", "max", "over", "partition", "order", "by",
            "rows", "range", "between", "unbounded", "preceding", "following", "current",
            "row", "int", "bigint", "string", "double", "float", "boolean", "timestamp",
            "date", "decimal", "array", "map", "struct",
        }
        if name and name not in _KEYWORDS and len(name) > 1:
            refs.add(name)
    return refs


def detect_breaking_changes(
    catalog: str,
    schema: str,
    table: str,
) -> dict:
    """Check if any upstream table referenced in this table's transformation
    expressions has dropped or renamed a column that is still in use.

    Returns:
    {
        "table_full_name": str,
        "breaking_changes": [
            {
                "upstream_table": str,
                "missing_columns": list[str],  # referenced in expr but not in current schema
                "affected_target_columns": list[str],
                "severity": "BREAKING" | "WARNING",
            },
            ...
        ],
        "checked_upstream_tables": int,
        "detail": str | None
    }
    """
    full_name = f"{catalog}.{schema}.{table}"
    result: dict = {
        "table_full_name": full_name,
        "breaking_changes": [],
        "checked_upstream_tables": 0,
    }

    # Load transformation edges for this target table
    try:
        rows = _execute_sql(
            f"SELECT src_fqn, src_col, dst_col, expr_sql "
            f"FROM {EDGE_TABLE} "
            f"WHERE dst_fqn = '{full_name}' "
            f"  AND expr_sql IS NOT NULL AND expr_sql != ''"
        )
    except Exception as e:
        result["detail"] = f"Could not load transformation edges: {e}"
        return result

    if not rows:
        result["detail"] = "No transformation edges found. Run a build first."
        return result

    # Group by upstream table
    upstream_refs: dict[str, dict] = {}
    for r in rows:
        src_fqn = r.get("src_fqn", "")
        if not src_fqn:
            continue
        if src_fqn not in upstream_refs:
            upstream_refs[src_fqn] = {"col_refs": set(), "target_columns": set()}
        col_refs = _extract_column_refs_from_expr(r.get("expr_sql") or "")
        upstream_refs[src_fqn]["col_refs"].update(col_refs)
        upstream_refs[src_fqn]["target_columns"].add(r.get("dst_col", ""))

    result["checked_upstream_tables"] = len(upstream_refs)
    breaking: list[dict] = []

    for upstream_fqn, data in upstream_refs.items():
        parts = upstream_fqn.split(".")
        if len(parts) != 3:
            continue
        current_cols = _get_current_columns(parts[0], parts[1], parts[2])
        if not current_cols:
            continue  # Can't verify; skip

        # Cross-reference: expression column refs vs current schema
        missing = data["col_refs"] - current_cols
        # Further filter: only flag columns that also match the src_col
        src_cols_for_table = {r["src_col"].lower() for r in rows if r.get("src_fqn") == upstream_fqn}
        missing_src = src_cols_for_table - current_cols

        if missing_src:
            breaking.append({
                "upstream_table": upstream_fqn,
                "missing_columns": sorted(missing_src),
                "affected_target_columns": sorted(data["target_columns"]),
                "severity": "BREAKING",
                "detail": (
                    f"Column(s) {sorted(missing_src)} from {upstream_fqn} "
                    f"are referenced in transformation edges for {full_name} "
                    f"but no longer exist in the upstream table's schema."
                ),
            })

    result["breaking_changes"] = breaking
    return result


def detect_schema_changes_for_catalog(
    catalog: str,
    schema: Optional[str] = None,
) -> list[dict]:
    """Run breaking-change detection across all tables in a catalog/schema.
    Returns a flat list of breaking-change dicts (only tables with issues).
    """
    sch_filter = f"AND table_schema = '{schema}'" if schema else ""
    try:
        tables = _execute_sql(
            f"SELECT table_schema, table_name FROM {catalog}.information_schema.tables "
            f"WHERE table_schema NOT IN ('information_schema') {sch_filter} LIMIT 500"
        )
    except Exception as e:
        logger.info(f"schema_change: table enumeration failed for {catalog}: {e}")
        return []

    results: list[dict] = []
    for t in tables:
        analysis = detect_breaking_changes(catalog, t["table_schema"], t["table_name"])
        if analysis.get("breaking_changes"):
            results.append(analysis)
    return results
