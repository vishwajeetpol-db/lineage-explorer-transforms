"""Impact analysis (blast radius) — capability 18.

Endpoints:
  GET /api/impact   — downstream count, consumer owners, sensitive data affected
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.server.governance import get_table_governance

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/impact", tags=["impact"])

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
IMPACT_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))
IMPACT_MAX_HOPS = int(os.environ.get("IMPACT_MAX_HOPS", "5"))

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_]{1,255}$")


def _validate(value: str, name: str) -> str:
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v


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


def _bfs_downstream(
    start_table: str,
    max_hops: int = IMPACT_MAX_HOPS,
) -> dict:
    """BFS walk over system.access.table_lineage to find all downstream tables.

    Returns {table_full_name: hop_distance}.
    """
    visited: dict[str, int] = {}
    frontier = [start_table]
    hop = 0
    while frontier and hop < max_hops:
        hop += 1
        quoted = ", ".join(f"'{t}'" for t in frontier)
        try:
            rows = _execute_sql(
                f"SELECT DISTINCT target_table_full_name "
                f"FROM system.access.table_lineage "
                f"WHERE source_table_full_name IN ({quoted}) "
                f"  AND event_time >= dateadd(DAY, -{IMPACT_LOOKBACK_DAYS}, current_timestamp())"
            )
        except Exception as e:
            logger.info(f"impact: BFS hop {hop} failed: {e}")
            break
        next_frontier = []
        for r in rows:
            t = r.get("target_table_full_name")
            if t and t != start_table and t not in visited:
                visited[t] = hop
                next_frontier.append(t)
        frontier = next_frontier
    return visited


@router.get("")
async def get_impact(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    max_hops: Optional[int] = Query(None),
):
    """Return blast-radius analysis for `catalog.schema.table`.

    Response includes:
      - downstream_count: total downstream tables within max_hops
      - consumer_owners: unique owners of downstream tables
      - sensitive_affected: downstream tables that themselves contain sensitive columns
      - downstream_tables: list of {full_name, hop_distance}
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    hops = min(max_hops or IMPACT_MAX_HOPS, 10)
    full_name = f"{c}.{s}.{t}"

    try:
        downstream = _bfs_downstream(full_name, max_hops=hops)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Enrich downstream tables with owner and sensitivity info (best-effort)
    consumer_owners: set[str] = set()
    sensitive_affected: list[str] = []
    downstream_list = []

    # Batch ownership lookup from information_schema
    all_tables = list(downstream.keys())
    owner_map: dict[str, str] = {}
    if all_tables:
        try:
            # Build a WHERE clause using full_name reconstruction
            fqn_list = ", ".join(f"'{fn}'" for fn in all_tables[:100])
            rows = _execute_sql(
                f"SELECT CONCAT(table_catalog,'.',table_schema,'.',table_name) AS full_name, "
                f"       table_owner "
                f"FROM system.information_schema.tables "
                f"WHERE CONCAT(table_catalog,'.',table_schema,'.',table_name) IN ({fqn_list})"
            )
            owner_map = {r["full_name"]: r.get("table_owner") or "" for r in rows}
        except Exception:
            pass

    for fn, hop_dist in sorted(downstream.items(), key=lambda x: x[1]):
        owner = owner_map.get(fn, "")
        if owner:
            consumer_owners.add(owner)
        # Quick sensitivity check via governance heuristics
        parts = fn.split(".")
        is_sensitive = False
        if len(parts) == 3:
            try:
                gov = get_table_governance(parts[0], parts[1], parts[2])
                is_sensitive = len(gov.get("sensitive_columns", [])) > 0
                if is_sensitive:
                    sensitive_affected.append(fn)
            except Exception:
                pass
        downstream_list.append({
            "full_name": fn,
            "hop_distance": hop_dist,
            "owner": owner or None,
            "has_sensitive_columns": is_sensitive,
        })

    return {
        "table_full_name": full_name,
        "max_hops": hops,
        "lookback_days": IMPACT_LOOKBACK_DAYS,
        "downstream_count": len(downstream),
        "consumer_owners": sorted(consumer_owners),
        "sensitive_affected_count": len(sensitive_affected),
        "sensitive_affected": sensitive_affected,
        "downstream_tables": downstream_list,
    }
