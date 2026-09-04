"""Column profiling overlay — capability 36.

Attaches lightweight column statistics (null %, distinct count, value
distribution) alongside lineage nodes so governance teams get immediate
data-quality context in the graph without opening a full profiler.

Two data sources (in priority order):
  1. Delta table statistics (from DESCRIBE TABLE EXTENDED / system-computed
     stats stored in the Delta log): fast, zero-cost, but only has count,
     null_count, and min/max if ANALYZE TABLE has been run.
  2. Live ad-hoc profiling query: runs a SELECT COUNT(*), COUNT(DISTINCT col),
     SUM(CASE WHEN col IS NULL THEN 1 ELSE 0 END) ... against the actual table.
     Gated behind `live_profile=True` to avoid unplanned warehouse costs.

All functions are non-fatal.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
PROFILE_ROW_LIMIT = int(os.environ.get("PROFILE_ROW_LIMIT", "10000000"))  # cap for live profile


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


# ---------------------------------------------------------------------------
# Delta stats (method 1 — free)
# ---------------------------------------------------------------------------

def _get_delta_stats(catalog: str, schema: str, table: str) -> dict:
    """Read column statistics stored in the Delta log (if ANALYZE TABLE was run)."""
    full_name = f"{catalog}.{schema}.{table}"
    stats: dict = {"source": "delta_stats", "columns": []}
    try:
        rows = _execute_sql(f"DESCRIBE EXTENDED `{catalog}`.`{schema}`.`{table}`")
        # DESCRIBE EXTENDED returns a flat key-value section after the column list.
        # Column statistics appear in rows like:
        #   col_name = 'Statistics', data_type = 'N rows'
        # We only need the row count here; full per-column stats require
        # information_schema.column_statistics (DBR 14.3+).
        for r in rows:
            col = str(r.get("col_name") or "").strip()
            val = str(r.get("data_type") or "").strip()
            if col == "Statistics":
                stats["row_count_approx"] = val
                break
    except Exception as e:
        logger.debug(f"column_profiling: Delta stats unavailable for {full_name}: {e}")
    # Try information_schema.column_statistics (available DBR 14.3+)
    try:
        col_stats = _execute_sql(
            f"SELECT column_name, null_count, distinct_count, avg_col_len, max_col_len "
            f"FROM {catalog}.information_schema.column_statistics "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}'"
        )
        stats["columns"] = [
            {
                "name": r["column_name"],
                "null_count": r.get("null_count"),
                "distinct_count": r.get("distinct_count"),
                "avg_col_len": r.get("avg_col_len"),
                "max_col_len": r.get("max_col_len"),
                "profile_source": "information_schema.column_statistics",
            }
            for r in col_stats
        ]
    except Exception as e:
        logger.debug(f"column_profiling: column_statistics not available for {full_name}: {e}")
    return stats


# ---------------------------------------------------------------------------
# Live ad-hoc profiling (method 2 — gated, costs a warehouse query)
# ---------------------------------------------------------------------------

def _get_live_profile(
    catalog: str,
    schema: str,
    table: str,
    columns: Optional[list[str]] = None,
) -> list[dict]:
    """Run a live profiling query against up to 10 columns."""
    full_name = f"`{catalog}`.`{schema}`.`{table}`"
    # Get column list from information_schema if not provided
    if not columns:
        try:
            col_rows = _execute_sql(
                f"SELECT column_name, data_type "
                f"FROM `{catalog}`.information_schema.columns "
                f"WHERE table_schema = '{schema}' AND table_name = '{table}' "
                f"ORDER BY ordinal_position LIMIT 10"
            )
            columns = [r["column_name"] for r in col_rows]
        except Exception:
            return []

    if not columns:
        return []

    # Build a single query with COUNT, NULL%, DISTINCT count per column
    col_exprs = []
    for c in columns[:10]:  # cap at 10 columns
        safe_c = c.replace("`", "")
        col_exprs.extend([
            f"COUNT(DISTINCT `{safe_c}`) AS `{safe_c}__distinct`",
            f"SUM(CASE WHEN `{safe_c}` IS NULL THEN 1 ELSE 0 END) AS `{safe_c}__null_count`",
        ])

    select_list = ", ".join(["COUNT(*) AS __total_rows"] + col_exprs)
    try:
        rows = _execute_sql(
            f"SELECT {select_list} FROM {full_name} "
            f"WHERE 1=1 LIMIT {PROFILE_ROW_LIMIT}"
        )
        if not rows:
            return []
        r = rows[0]
        total = int(r.get("__total_rows") or 0)
        profiles: list[dict] = []
        for c in columns[:10]:
            safe_c = c.replace("`", "")
            distinct = int(r.get(f"{safe_c}__distinct") or 0)
            null_count = int(r.get(f"{safe_c}__null_count") or 0)
            null_pct = round((null_count / total * 100), 2) if total > 0 else None
            profiles.append({
                "name": c,
                "total_rows": total,
                "distinct_count": distinct,
                "null_count": null_count,
                "null_pct": null_pct,
                "profile_source": "live_query",
            })
        return profiles
    except Exception as e:
        logger.info(f"column_profiling: live profile failed for {catalog}.{schema}.{table}: {e}")
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_column_profile(
    catalog: str,
    schema: str,
    table: str,
    columns: Optional[list[str]] = None,
    live_profile: bool = False,
) -> dict:
    """Return a column profiling overlay for `catalog.schema.table`.

    `live_profile=True` runs an ad-hoc query against the table; use sparingly.
    Without it, only Delta stats (from ANALYZE TABLE runs) are returned.

    Response:
    {
        "table_full_name": str,
        "row_count_approx": str | None,
        "profile_source": "delta_stats" | "live_query" | "mixed",
        "columns": [
            {"name": str, "distinct_count": int|None, "null_pct": float|None, ...},
            ...
        ]
    }
    """
    full_name = f"{catalog}.{schema}.{table}"
    result: dict = {
        "table_full_name": full_name,
        "row_count_approx": None,
        "profile_source": "none",
        "columns": [],
    }

    # Method 1: Delta stats (always attempted)
    delta_stats = _get_delta_stats(catalog, schema, table)
    result["row_count_approx"] = delta_stats.get("row_count_approx")
    delta_cols = delta_stats.get("columns", [])

    if live_profile:
        # Method 2: live query (only for explicitly requested columns or all)
        live_cols = _get_live_profile(catalog, schema, table, columns)
        if live_cols:
            # Merge: live profile is more accurate; fill in delta fields where live has gaps
            delta_by_name = {c["name"]: c for c in delta_cols}
            for lc in live_cols:
                dc = delta_by_name.get(lc["name"], {})
                merged = {**dc, **lc}  # live overrides delta
                result["columns"].append(merged)
            result["profile_source"] = "live_query"
        else:
            result["columns"] = delta_cols
            result["profile_source"] = "delta_stats" if delta_cols else "none"
    else:
        result["columns"] = delta_cols
        result["profile_source"] = "delta_stats" if delta_cols else "none"

    return result
