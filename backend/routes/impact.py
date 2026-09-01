"""Impact analysis (blast radius) — capability 18.

Endpoints:
  GET /api/impact   — downstream count, consumer owners, sensitive data affected
"""
from __future__ import annotations

import os
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client, LINEAGE_WINDOW_DAYS
from backend.server.governance import get_table_governance
from backend.server.entities import resolve_entities

CONSUMER_CAP = 200

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/impact", tags=["impact"])

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
# Single source of truth: lineage_service owns this window (default 365).
# Re-reading LINEAGE_WINDOW_DAYS with a local default of 90 made impact analysis
# disagree with the graph whenever the env var was unset.
IMPACT_LOOKBACK_DAYS = LINEAGE_WINDOW_DAYS
IMPACT_MAX_HOPS = int(os.environ.get("IMPACT_MAX_HOPS", "5"))

_IDENTIFIER_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,255}$")  # canonical: backend/validators.py


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


def _consumers(scope_tables: list[str], resolve: bool = True) -> dict:
    """Entities (dashboards/jobs/pipelines/notebooks/queries) that READ any table
    in `scope_tables` — the blast-radius consumers. Grouped by type, with each
    entity resolved to a display name + deep link so the UI can render clickable
    consumer chips (like the reference tool)."""
    if not scope_tables:
        return {"by_type": {}, "total": 0, "entities": []}
    quoted = ", ".join(f"'{t}'" for t in scope_tables[:200])
    try:
        rows = _execute_sql(
            f"SELECT DISTINCT entity_type, entity_id "
            f"FROM system.access.table_lineage "
            f"WHERE source_table_full_name IN ({quoted}) "
            f"  AND entity_type IS NOT NULL AND entity_id IS NOT NULL "
            f"  AND event_time >= dateadd(DAY, -{IMPACT_LOOKBACK_DAYS}, current_timestamp()) "
            f"LIMIT {CONSUMER_CAP}"
        )
    except Exception as e:
        logger.info(f"impact: consumer query failed: {e}")
        return {"by_type": {}, "total": 0, "entities": []}

    by_type: dict[str, int] = {}
    raw = []
    for r in rows:
        et = r.get("entity_type")
        eid = r.get("entity_id")
        if not et or not eid:
            continue
        by_type[et] = by_type.get(et, 0) + 1
        raw.append({"entity_type": et, "entity_id": str(eid)})

    entities = raw
    if resolve and raw:
        try:
            entities = resolve_entities(raw[:80])  # bounded name/link resolution
        except Exception:
            pass
    return {"by_type": by_type, "total": len(raw), "entities": entities}


def _compute_impact(c: str, s: str, t: str, hops: int) -> dict:
    """Compute blast-radius analysis for a table (the cacheable payload)."""
    full_name = f"{c}.{s}.{t}"
    downstream = _bfs_downstream(full_name, max_hops=hops)

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

    # Consumers = entities that read the focus table OR any downstream table.
    consumers = _consumers([full_name] + all_tables)

    return {
        "table_full_name": full_name,
        "max_hops": hops,
        "lookback_days": IMPACT_LOOKBACK_DAYS,
        "downstream_count": len(downstream),
        "consumer_owners": sorted(consumer_owners),
        "consumers": consumers,
        "sensitive_affected_count": len(sensitive_affected),
        "sensitive_affected": sensitive_affected,
        "downstream_tables": downstream_list,
    }


@router.get("")
async def get_impact(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    max_hops: Optional[int] = Query(None),
    refresh: bool = Query(False),
):
    """Return blast-radius analysis for `catalog.schema.table`.

    Served from the per-table capability cache unless `refresh=true`. Response
    includes downstream_count, consumer_owners, sensitive_affected, and a
    downstream_tables list, plus a `_cache` meta block.
    """
    c = _validate(catalog, "catalog")
    s = _validate(schema, "schema")
    t = _validate(table, "table")
    hops = min(max_hops or IMPACT_MAX_HOPS, 10)
    fqn = f"{c}.{s}.{t}"
    from backend.main import _get_user_info
    from backend.server.capability_cache import serve_or_compute
    email, _ = _get_user_info(request)
    try:
        return await asyncio.to_thread(
            serve_or_compute, fqn, "impact",
            lambda: _compute_impact(c, s, t, hops), email or "", refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
