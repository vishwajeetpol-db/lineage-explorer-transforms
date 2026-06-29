"""Transformation Lineage Service — reads pre-built column-level transformation
edges from Delta tables and performs BFS backtracking to produce upstream
transformation graphs.

This service reads from the lineage_edge_endpoints table materialized by the
transformation lineage pipeline (run_all notebook). It does NOT parse source
code at query time — that heavy lifting happens asynchronously in a serverless
job (see build_service.py).

Integrates with the shared cache infrastructure from lineage_service.py for
single-flight coalescing and memory-bounded TTL caching.

Performance optimizations (v3):
  - Single-query freshness check (COUNT + MAX in one pass — eliminates double roundtrip)
  - Pre-indexed upstream adjacency (built once per table, cached)
  - Pushdown pipeline_run_id predicate (avoids full table scan)
  - Lightweight size estimation (avoids re-serialization)
  - Parallelized edge loading + category resolution via ThreadPoolExecutor
  - Single-flight coalescing with bounded per-key lock pool
"""

import os
import sys
import time
import logging
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Optional

from cachetools import TTLCache
from databricks.sdk.service.sql import StatementState

from backend.lineage_service import _get_client
from backend.models import (
    TransformNode,
    TransformEdge,
    TransformLevel,
    TransformResponse,
    FreshnessInfo,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration (from environment)
# ---------------------------------------------------------------------------
LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
TRANSFORM_CACHE_TTL = int(os.environ.get("TRANSFORM_CACHE_TTL_SECONDS", "3600"))
BUILD_CACHE_TTL_HOURS = int(os.environ.get("BUILD_CACHE_TTL_HOURS", "24"))
TRANSFORM_MAX_DEPTH = int(os.environ.get("TRANSFORM_MAX_DEPTH", "8"))
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")

EDGE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.lineage_edge_endpoints"

# ---------------------------------------------------------------------------
# Thread pool for parallel SQL execution within transform queries
# ---------------------------------------------------------------------------
_TRANSFORM_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="transform")

# ---------------------------------------------------------------------------
# Transform-specific cache (separate from main lineage cache to allow
# different TTL — transform data changes more frequently via builds)
# ---------------------------------------------------------------------------
_TRANSFORM_CACHE_MAX_MB = 64
_TRANSFORM_CACHE_MAX_BYTES = _TRANSFORM_CACHE_MAX_MB * 1024 * 1024


class _TCacheEntry:
    __slots__ = ("value", "size_bytes", "created_at")

    def __init__(self, value, size_bytes: int):
        self.value = value
        self.size_bytes = size_bytes
        self.created_at = time.time()


def _t_entry_size(entry: _TCacheEntry) -> int:
    return max(1, entry.size_bytes)


_transform_cache: TTLCache = TTLCache(
    maxsize=_TRANSFORM_CACHE_MAX_BYTES,
    ttl=TRANSFORM_CACHE_TTL,
    getsizeof=_t_entry_size,
)
_transform_cache_lock = threading.RLock()

# Single-flight locks for transform queries
_transform_flights: "OrderedDict[str, threading.Lock]" = OrderedDict()
_transform_flights_guard = threading.Lock()
_TRANSFORM_FLIGHTS_MAX = 512


def _get_transform_lock(key: str) -> threading.Lock:
    with _transform_flights_guard:
        lock = _transform_flights.get(key)
        if lock is None:
            lock = threading.Lock()
            _transform_flights[key] = lock
        else:
            _transform_flights.move_to_end(key)
        while len(_transform_flights) > _TRANSFORM_FLIGHTS_MAX:
            oldest_key, oldest_lock = next(iter(_transform_flights.items()))
            if oldest_lock.locked():
                _transform_flights.move_to_end(oldest_key)
                break
            _transform_flights.popitem(last=False)
        return lock


def _estimate_size(result) -> int:
    """Lightweight size estimation — avoids full JSON serialization.

    Uses structural heuristic: count fields/items and apply per-type byte weights.
    This is 10-50x faster than json.dumps(model_dump()) for large responses.
    """
    if hasattr(result, "model_dump"):
        try:
            d = result.model_dump()
            size = 200  # base overhead
            for v in d.values():
                if isinstance(v, list):
                    size += len(v) * 300
                elif isinstance(v, str):
                    size += len(v)
                elif isinstance(v, dict):
                    size += len(v) * 150
                else:
                    size += 50
            return int(size * 1.3)
        except Exception:
            return 4096
    elif isinstance(result, dict):
        # Avoid json.dumps — estimate from key count
        size = 100
        for v in result.values():
            if isinstance(v, list):
                size += len(v) * 250
            elif isinstance(v, str):
                size += len(v)
            else:
                size += 50
        return int(size * 1.3)
    elif isinstance(result, list):
        return max(100, len(result) * 200)
    return sys.getsizeof(result)


def _transform_cached_fetch(key: str, fetcher):
    """Single-flight + TTL cache for transform queries."""
    with _transform_cache_lock:
        try:
            entry = _transform_cache[key]
            return entry.value
        except KeyError:
            pass

    lock = _get_transform_lock(key)
    with lock:
        # Double-check after acquiring per-key lock
        with _transform_cache_lock:
            try:
                entry = _transform_cache[key]
                return entry.value
            except KeyError:
                pass

        result = fetcher()
        size = _estimate_size(result)

        entry = _TCacheEntry(result, size)
        with _transform_cache_lock:
            _transform_cache[key] = entry

        return result


def invalidate_transform_cache():
    """Clear the entire transform cache (called on build completion)."""
    with _transform_cache_lock:
        _transform_cache.clear()
    logger.info("Transform cache invalidated")


# Stored transformation-lineage tables wiped by a global "clear". The audit-scan
# (notebook_path) and LLM (pyspark_to_sql) caches are deliberately retained — they
# are expensive to rebuild and aren't lineage; clearing code_versions forces the
# next build to fully re-parse.
_CLEARABLE_STORE_TABLES = (
    "lineage_edge_endpoints", "lineage_nodes", "lineage_edges",
    "lineage_code_versions", "lineage_raw_code", "lineage_parse_metrics",
    "lineage_graph_cache", "lineage_reconciliation", "lineage_extraction_reports",
    "lineage_sublineage_cache",
)


def clear_transform_lineage(scope: str = "cache", table_fqn: Optional[str] = None) -> dict:
    """Invalidate the in-memory transform caches and (optionally) the stored edges.

    scope:
      - "cache": flush in-memory caches only (no data loss).
      - "table": also delete the given table's stored edges/nodes (then it shows
        as "not built" until rebuilt). Requires a validated `table_fqn`.
      - "all":   wipe every stored transformation-lineage table (start fresh).
    Always flushes the in-memory cache so the next read re-queries.
    """
    invalidate_transform_cache()
    schema_prefix = EDGE_TABLE.rsplit(".", 1)[0]  # catalog.schema
    cleared: list[str] = []

    if scope == "table":
        if not table_fqn:
            return {"scope": scope, "error": "table_fqn required"}
        _sql(f"DELETE FROM {EDGE_TABLE} WHERE src_fqn = '{table_fqn}' OR dst_fqn = '{table_fqn}'")
        cleared.append("lineage_edge_endpoints")
        try:
            _sql(f"DELETE FROM {schema_prefix}.lineage_nodes WHERE table_fqn = '{table_fqn}'")
            _sql(
                f"DELETE FROM {schema_prefix}.lineage_edges "
                f"WHERE src_id LIKE 'col:{table_fqn}::%' OR dst_id LIKE 'col:{table_fqn}::%'"
            )
            cleared += ["lineage_nodes", "lineage_edges"]
        except Exception as e:  # noqa: BLE001
            logger.warning("per-table clear (nodes/edges) failed for %s: %s", table_fqn, e)
        logger.info("Cleared transformation lineage for table %s", table_fqn)
        return {"scope": scope, "table_fqn": table_fqn, "cleared": cleared}

    if scope == "all":
        for t in _CLEARABLE_STORE_TABLES:
            try:
                _sql(f"DELETE FROM {schema_prefix}.{t}")
                cleared.append(t)
            except Exception as e:  # noqa: BLE001
                logger.warning("clear store table %s failed: %s", t, e)
        logger.info("Wiped all stored transformation lineage (%d tables)", len(cleared))
        return {"scope": scope, "cleared": cleared}

    return {"scope": "cache", "cleared": cleared}


# ---------------------------------------------------------------------------
# SQL execution helper (reuses the shared WorkspaceClient)
# ---------------------------------------------------------------------------
def _sql(stmt: str) -> tuple[list[str], list[list]]:
    """Execute SQL via the Statement Execution API and return (columns, rows)."""
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=stmt,
        wait_timeout=SQL_WAIT_TIMEOUT,
    )
    if resp.status and resp.status.state != StatementState.SUCCEEDED:
        error_msg = resp.status.error.message if resp.status.error else "SQL error"
        raise RuntimeError(f"Transform SQL failed: {error_msg}")
    cols = [c.name for c in (resp.manifest.schema.columns or [])]
    rows = (resp.result.data_array if resp.result else None) or []
    return cols, rows


# ---------------------------------------------------------------------------
# Transformation category colors (matching LATTICE's visual language)
# ---------------------------------------------------------------------------
TRANSFORM_CATEGORIES = {
    "ARITHMETIC": "#FF4433",
    "WINDOW": "#A855F7",
    "TYPE CAST": "#10B981",
    "CAST": "#10B981",
    "AGGREGATE": "#3B82F6",
    "AGGREGATION": "#3B82F6",
    "STATISTICAL": "#F59E0B",
    "PROJECTION": "#6366F1",
    "PASSTHROUGH": "#6B7280",
    "FILTER": "#EC4899",
    "JOIN": "#06B6D4",
    "CONDITIONAL": "#F472B6",
    "OTHER": "#9CA3AF",
    "UNKNOWN": "#6B7280",
}

LEVEL_COLORS = [
    "#FF4433", "#3B82F6", "#A855F7", "#10B981",
    "#F59E0B", "#6366F1", "#EC4899", "#06B6D4",
]


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------
def _resolve_edge_table(
    catalog: Optional[str] = None, schema: Optional[str] = None
) -> str:
    """Resolve the edge endpoints table from a caller-supplied catalog/schema.

    Option A — single dedicated lineage store: ALL transformation edges live in
    one app-SP-owned store (LINEAGE_CATALOG.LINEAGE_SCHEMA), regardless of which
    data catalog the selected table belongs to. Node-ids are fully qualified
    (col:<data_catalog>.<schema>.<table>::<col>), so one store serves every data
    catalog and the app SP never needs write access on production data catalogs.

    The catalog/schema args (the *selected table's* location) are intentionally
    NOT used for the store path — they only matter for building the node-id that
    the trace backtracks from. Retained for call-site compatibility.
    """
    return EDGE_TABLE


def get_transform_freshness(catalog: str, schema: str, table: str) -> FreshnessInfo:
    """Check if transformation lineage exists for a table and whether it's stale.

    Optimized v3: SINGLE query combining COUNT + MAX (eliminates the double-roundtrip
    of the v2 exists-then-count pattern). For tables WITHOUT lineage, this single query
    returns cnt=0 just as fast as the old EXISTS check because the query planner short-
    circuits on the WHERE filter. For tables WITH lineage, this is 2x faster (1 query
    vs 2 sequential queries).

    The edge table is derived from the supplied catalog/schema so reads hit
    <catalog>.<schema>.lineage_edge_endpoints (the same catalog as the selected table).
    """
    fqn = f"{catalog}.{schema}.{table}"
    edge_table = _resolve_edge_table(catalog, schema)
    cache_key = f"transform_fresh:{edge_table}:{fqn}"

    def _fetch():
        try:
            _, rows = _sql(f"""
                SELECT COUNT(*) AS cnt, MAX(materialized_at) AS last_built
                FROM {edge_table}
                WHERE src_fqn = '{fqn}' OR dst_fqn = '{fqn}'
            """)
            if not rows or not rows[0]:
                return FreshnessInfo(
                    exists=False, edge_count=0, last_built=None,
                    age_str="Never built", is_stale=True
                )

            edge_count = int(rows[0][0] or 0)
            last_built_str = str(rows[0][1]) if rows[0][1] else None

            if edge_count == 0:
                return FreshnessInfo(
                    exists=False, edge_count=0, last_built=None,
                    age_str="Never built", is_stale=True
                )

            # Compute staleness
            age_str = last_built_str[:16] if last_built_str else "Unknown"
            is_stale = True
            if last_built_str:
                try:
                    last_built = datetime.fromisoformat(last_built_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    delta = now - last_built

                    if delta < timedelta(minutes=60):
                        age_str = f"{int(delta.total_seconds() // 60)}m ago"
                    elif delta < timedelta(hours=24):
                        age_str = f"{int(delta.total_seconds() // 3600)}h ago"
                    else:
                        age_str = f"{delta.days}d ago"

                    is_stale = delta > timedelta(hours=BUILD_CACHE_TTL_HOURS)
                except Exception:
                    is_stale = True

            return FreshnessInfo(
                exists=True,
                edge_count=edge_count,
                last_built=last_built_str,
                age_str=age_str,
                is_stale=is_stale,
            )
        except Exception as e:
            logger.warning(f"Freshness check failed for {fqn}: {e}")
            return FreshnessInfo(
                exists=False, edge_count=0, last_built=None,
                age_str="Unknown", is_stale=True
            )

    return _transform_cached_fetch(cache_key, _fetch)


def _get_latest_run_id(
    edge_table: Optional[str] = None, table_fqn: Optional[str] = None
) -> Optional[str]:
    """Resolve the pipeline_run_id whose edges should serve `table_fqn`.

    Builds are scoped to a single table (KPI_TABLES = the clicked table), so each
    run materializes edges for ONLY that table. A naive global "latest run by
    materialized_at" therefore serves whichever table was built most recently and
    hides every other table — e.g. building the MV made dim_customers.full_name
    return nothing, even though its edges were never deleted.

    The fix: pick the latest run where THIS table is a build target (`dst_fqn`),
    so each table is served from the run that actually produced its columns. If
    the table is never a destination (a pure upstream source), fall back to the
    latest run where it appears as a source so source-column detection still
    works. With no `table_fqn`, retain the global-latest behavior.
    """
    edge_table = edge_table or EDGE_TABLE
    cache_key = f"transform_latest_run_id:{edge_table}:{table_fqn or '__global__'}"

    def _fetch():
        if table_fqn:
            # Prefer the latest run where the table is a destination; among runs
            # that only reference it as a source, pick the latest of those.
            _, rows = _sql(f"""
                SELECT pipeline_run_id
                FROM {edge_table}
                WHERE src_fqn = '{table_fqn}' OR dst_fqn = '{table_fqn}'
                GROUP BY pipeline_run_id
                ORDER BY
                    MAX(CASE WHEN dst_fqn = '{table_fqn}' THEN 1 ELSE 0 END) DESC,
                    MAX(materialized_at) DESC
                LIMIT 1
            """)
        else:
            _, rows = _sql(f"""
                SELECT pipeline_run_id
                FROM {edge_table}
                ORDER BY materialized_at DESC
                LIMIT 1
            """)
        if rows and rows[0][0]:
            return str(rows[0][0])
        return None

    return _transform_cached_fetch(cache_key, _fetch)


def load_edges(
    table_fqn: Optional[str] = None, edge_table: Optional[str] = None
) -> list[dict]:
    """Load transformation edges from the latest pipeline run.
    Optionally filtered to edges touching a specific table.

    Optimized: resolves latest run_id separately (cached), then uses direct
    equality predicate instead of correlated subquery / CTE.
    """
    edge_table = edge_table or EDGE_TABLE
    cache_key = f"transform_edges:{edge_table}:{table_fqn or 'all'}"

    def _fetch():
        # Resolve the run that actually built THIS table (not the global-latest
        # run, which would be whichever table was generated most recently).
        run_id = _get_latest_run_id(edge_table, table_fqn)
        if not run_id:
            return []

        where_parts = [f"pipeline_run_id = '{run_id}'"]
        where_parts.append("src_fqn IS NOT NULL AND dst_fqn IS NOT NULL")
        where_parts.append("src_col IS NOT NULL AND dst_col IS NOT NULL")
        # Drop self-loops (src column node == dst column node). These come from
        # stale/mis-resolved parses attributing a source ref to the output table
        # itself; they render as a target with no upstream and, sharing an edge_id
        # with the real edge, can shadow it. Filtering here fixes already-stored
        # data without a rebuild.
        where_parts.append("src_node_id != dst_node_id")

        if table_fqn:
            where_parts.append(f"(src_fqn = '{table_fqn}' OR dst_fqn = '{table_fqn}')")

        sql = f"""
            SELECT
                src_node_id, src_fqn, src_col,
                dst_node_id, dst_fqn, dst_col,
                edge_id, source_path, expr, expr_sql, transform_category
            FROM {edge_table}
            WHERE {' AND '.join(where_parts)}
        """
        cols, rows = _sql(sql)
        return [dict(zip(cols, r)) for r in rows]

    return _transform_cached_fetch(cache_key, _fetch)


def backtrack_transform_lineage(
    catalog: str,
    schema: str,
    table: str,
    column: str,
    max_depth: Optional[int] = None,
) -> TransformResponse:
    """BFS backtrack from a target column through transformation edges.

    Returns a layered graph structure suitable for rendering as a vertical DAG:
    Level 0 = target column, Level N = Nth upstream layer.

    Optimized v3:
      - Edges loaded once per table (cached), index built in-memory
      - BFS uses set operations for O(1) frontier membership
      - Early exit when frontier is empty
      - Pre-allocated data structures (avoid realloc on append)
      - Index built with defaultdict pattern for speed
    """
    if max_depth is None:
        max_depth = TRANSFORM_MAX_DEPTH

    fqn = f"{catalog}.{schema}.{table}"
    edge_table = _resolve_edge_table(catalog, schema)
    target_node_id = f"col:{fqn}::{column}"
    cache_key = f"transform_trace:{edge_table}:{fqn}::{column}::{max_depth}"

    def _fetch():
        start_ts = time.time()

        # Load all edges for this table (cached after first call)
        edges = load_edges(fqn, edge_table)
        if not edges:
            return TransformResponse(
                levels=[],
                has_lineage=False,
                fetch_duration_ms=int((time.time() - start_ts) * 1000),
            )

        # Build upstream index: dst_node_id -> [edges flowing INTO it]
        # Using dict.setdefault is faster than defaultdict for this pattern
        upstream_idx: dict[str, list[dict]] = {}
        all_sources: set[str] = set()
        for e in edges:
            dst = e["dst_node_id"]
            if dst in upstream_idx:
                upstream_idx[dst].append(e)
            else:
                upstream_idx[dst] = [e]
            all_sources.add(e["src_node_id"])

        # Check if target column exists in the edge data
        if target_node_id not in upstream_idx:
            is_source = target_node_id in all_sources
            return TransformResponse(
                levels=[],
                has_lineage=False,
                is_source_column=is_source,
                fetch_duration_ms=int((time.time() - start_ts) * 1000),
            )

        # BFS backtracking — optimized with pre-allocated structures
        levels: list[TransformLevel] = []
        levels.append(TransformLevel(
            depth=0,
            label="Target Column",
            color=LEVEL_COLORS[0],
            nodes=[TransformNode(
                node_id=target_node_id,
                table_fqn=fqn,
                column=column,
            )],
            transforms=[],
        ))

        visited: set[str] = {target_node_id}
        frontier: set[str] = {target_node_id}

        for depth in range(1, max_depth + 1):
            if not frontier:
                break

            level_nodes: list[TransformNode] = []
            level_transforms: list[TransformEdge] = []
            next_frontier: set[str] = set()
            seen_edges: set[str] = set()

            for node_id in frontier:
                node_edges = upstream_idx.get(node_id)
                if not node_edges:
                    continue

                for edge in node_edges:
                    src_id = edge["src_node_id"]
                    # Dedup by the (source, target) column pair, not edge_id: the
                    # parser can emit several rows for the same hop (e.g. a bare
                    # `col` and an alias-qualified `t.col` reference), which would
                    # otherwise render as duplicate parallel edges in the popup.
                    edge_key = f"{src_id}|{node_id}"

                    if edge_key in seen_edges:
                        continue
                    seen_edges.add(edge_key)

                    # Record transform edge
                    category = (edge.get("transform_category") or "UNKNOWN").upper()
                    level_transforms.append(TransformEdge(
                        source_node_id=src_id,
                        target_node_id=node_id,
                        expression=edge.get("expr_sql") or edge.get("expr") or "--",
                        category=category,
                        category_color=TRANSFORM_CATEGORIES.get(category, "#6B7280"),
                        source_file=edge.get("source_path") or "",
                    ))

                    # Discover new upstream node
                    if src_id not in visited:
                        visited.add(src_id)
                        next_frontier.add(src_id)

                        src_tbl = edge.get("src_fqn") or "?"
                        src_col = edge.get("src_col") or "?"
                        level_nodes.append(TransformNode(
                            node_id=src_id,
                            table_fqn=src_tbl,
                            column=src_col,
                        ))

            if level_nodes or level_transforms:
                levels.append(TransformLevel(
                    depth=depth,
                    label=f"Upstream Layer {depth}",
                    color=LEVEL_COLORS[min(depth, len(LEVEL_COLORS) - 1)],
                    nodes=level_nodes,
                    transforms=level_transforms,
                ))

            frontier = next_frontier

        has_lineage = len(levels) > 1
        duration_ms = int((time.time() - start_ts) * 1000)

        return TransformResponse(
            levels=levels,
            has_lineage=has_lineage,
            fetch_duration_ms=duration_ms,
            total_nodes=len(visited),
            total_edges=sum(len(lv.transforms) for lv in levels),
            max_depth_reached=len(levels) - 1,
        )

    return _transform_cached_fetch(cache_key, _fetch)


def get_transform_categories() -> dict[str, str]:
    """Return the category → color mapping for the frontend legend."""
    return TRANSFORM_CATEGORIES


def get_transform_cache_snapshot() -> dict:
    """Return cache stats for the admin dashboard."""
    with _transform_cache_lock:
        return {
            "entries": len(_transform_cache),
            "current_size_mb": round(_transform_cache.currsize / (1024 * 1024), 2),
            "max_size_mb": _TRANSFORM_CACHE_MAX_MB,
            "ttl_seconds": TRANSFORM_CACHE_TTL,
        }
