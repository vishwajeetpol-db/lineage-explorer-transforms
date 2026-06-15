"""
Lineage service — queries Unity Catalog system tables to build lineage graphs.

Required SPN privileges:
  - USE CATALOG on target catalog
  - BROWSE on target catalog
  - USE SCHEMA on target schema(s)
  - CAN_USE on the SQL warehouse
  - USE CATALOG on system catalog
  - USE SCHEMA on system.access
  - SELECT on system.access (for table_lineage and column_lineage)

Lineage data comes exclusively from system.access.table_lineage and
system.access.column_lineage — the source of truth captured by Unity Catalog
from actual query execution. No inference, no heuristics, no regex parsing.
"""

import json
import os
import re
import sys
import time
import logging
import threading
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Callable, TypeVar
from cachetools import TTLCache
from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState
from backend.models import (
    TableNode,
    EntityNode,
    LineageEdge,
    ColumnLineageEdge,
    LineageResponse,
    ColumnLineageResponse,
    SharedOutEntry,
    ForeignCatalogEntry,
    SharingOverlay,
)

T = TypeVar("T")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton WorkspaceClient — avoids per-request auth handshake overhead
# ---------------------------------------------------------------------------
_client_instance: WorkspaceClient | None = None


def _get_client() -> WorkspaceClient:
    global _client_instance
    if _client_instance is None:
        _client_instance = WorkspaceClient()
    return _client_instance


# ---------------------------------------------------------------------------
# TTL cache with request coalescing (single-flight pattern).
#
# Backed by cachetools.TTLCache (LRU + TTL, memory-sized via getsizeof).
# Single-flight uses a per-key threading.Lock with double-checked reads:
# when N threads race on an empty key, N-1 block on the lock, and after
# the leader populates the cache they each find the value on re-check.
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "28800"))  # default 8 hours
CACHE_MAX_ENTRIES = int(os.environ.get("CACHE_MAX_ENTRIES", "20000"))  # secondary safety valve
CACHE_MAX_MEMORY_MB = int(os.environ.get("CACHE_MAX_MEMORY_MB", "250"))  # primary limit
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")  # max 50s per Databricks API limit (0s or 5-50s)
# Safety cap for catalog-wide lineage: a catalog with thousands of tables would
# produce a graph too large to lay out in the browser and too big for the cache.
# When the in-scope table count exceeds this, we refuse rather than melt the
# warehouse/browser. Schema-scoped requests are never capped.
LINEAGE_MAX_NODES = int(os.environ.get("LINEAGE_MAX_NODES", "2500"))
# Lookback window for system.access lineage queries. UC retains lineage events
# but the app only surfaces those within this window; older-only tables show as
# orphan. Configurable so aged demos/datasets can still be visualized.
LINEAGE_WINDOW_DAYS = int(os.environ.get("LINEAGE_WINDOW_DAYS", "90"))

# Lakeflow/DLT pipelines create internal backing tables alongside each
# materialized view / streaming table (`__materialization_mat_*`, `event_log_*`,
# `__apply_changes*`). They aren't user assets and have no real lineage, so
# they'd otherwise render as orphan noise nodes. Exclude them from every table
# listing. The lineage edges themselves reference the real MV/ST names, so this
# only filters the information_schema.tables enumeration.
_INTERNAL_TABLE_FILTER = (
    "table_name NOT RLIKE '^(__materialization_mat_|event_log_|__apply_changes)'"
)

# Conservative whitelist for entity ids interpolated into system-table queries
# (job ids, pipeline/notebook/dashboard ids, notebook paths). No quotes/semicolons.
_SAFE_ENTITY_ID_RE = re.compile(r"^[A-Za-z0-9_./@ +:-]{1,512}$")

# Same exclusion, applied to the lineage query's full-name columns — these
# internal tables also appear as source/target of pipeline write events, so they
# leak into the graph as edge-derived nodes unless filtered here too. Matches the
# internal prefix in the final name segment (after the last dot).
_INTERNAL_NAME_RE = r"[.](__materialization_mat_|event_log_|__apply_changes)"


def _internal_lineage_filter() -> str:
    return (
        f"(source_table_full_name IS NULL OR source_table_full_name NOT RLIKE '{_INTERNAL_NAME_RE}') "
        f"AND (target_table_full_name IS NULL OR target_table_full_name NOT RLIKE '{_INTERNAL_NAME_RE}')"
    )

_CACHE_MAX_BYTES = CACHE_MAX_MEMORY_MB * 1024 * 1024


class _CacheEntry:
    __slots__ = ("value", "size_bytes", "created_at", "last_accessed")

    def __init__(self, value: object, size_bytes: int):
        self.value = value
        self.size_bytes = size_bytes
        now = time.time()
        self.created_at = now
        self.last_accessed = now


def _estimate_value_size(val: object) -> int:
    """Estimate memory footprint of a cache value. Computed once at insert time.
    Uses JSON byte length * 2.5 to approximate Python object overhead."""
    try:
        if hasattr(val, 'model_dump'):
            raw = json.dumps(val.model_dump(), default=str)
        elif isinstance(val, (dict, list)):
            raw = json.dumps(val, default=str)
        else:
            return sys.getsizeof(val)
        return int(len(raw.encode('utf-8')) * 2.5)
    except Exception:
        return 1024  # conservative 1KB fallback


def _entry_size(entry: _CacheEntry) -> int:
    return max(1, entry.size_bytes)


# Memory-bounded TTL+LRU cache. cachetools auto-evicts LRU when currsize > maxsize.
_cache: TTLCache[str, _CacheEntry] = TTLCache(
    maxsize=_CACHE_MAX_BYTES,
    ttl=CACHE_TTL_SECONDS,
    getsizeof=_entry_size,
)
_cache_lock = threading.RLock()

# Per-key locks for single-flight. LRU-bounded so we don't leak a lock
# per cache key forever (each unique `columns:*` key would otherwise stick
# around for the lifetime of the process).
_KEYED_LOCKS_MAX = max(1024, CACHE_MAX_ENTRIES)
_keyed_locks: "OrderedDict[str, threading.Lock]" = OrderedDict()
_keyed_locks_guard = threading.Lock()


def _get_keyed_lock(key: str) -> threading.Lock:
    with _keyed_locks_guard:
        lock = _keyed_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _keyed_locks[key] = lock
        else:
            _keyed_locks.move_to_end(key)
        # Evict oldest unlocked locks once we exceed the cap.
        while len(_keyed_locks) > _KEYED_LOCKS_MAX:
            oldest_key, oldest_lock = next(iter(_keyed_locks.items()))
            if oldest_lock.locked():
                # Don't evict an in-use lock; rotate it to most-recent and stop.
                _keyed_locks.move_to_end(oldest_key)
                break
            _keyed_locks.popitem(last=False)
        return lock


def _cache_get(key: str):
    """Return cached value if present and fresh; None otherwise. Promotes LRU + updates last_accessed."""
    with _cache_lock:
        try:
            entry = _cache[key]  # __getitem__ bumps LRU + checks TTL expiry
        except KeyError:
            return None
        entry.last_accessed = time.time()
        return entry.value


def _cache_get_ts(key: str) -> float | None:
    """Return created_at timestamp of a cache entry, or None."""
    with _cache_lock:
        try:
            return _cache[key].created_at
        except KeyError:
            return None


def _cache_set(key: str, val: object) -> None:
    """Store a value. TTLCache handles memory-based LRU eviction; we additionally
    enforce the entry-count cap as a safety valve."""
    entry = _CacheEntry(val, _estimate_value_size(val))
    with _cache_lock:
        _cache[key] = entry
        while len(_cache) > CACHE_MAX_ENTRIES:
            try:
                evicted_key, evicted = _cache.popitem()
                logger.info(
                    f"Cache count-cap eviction: {evicted_key} "
                    f"({evicted.size_bytes / 1024:.1f}KB freed)"
                )
            except KeyError:
                break


def _cached_fetch(key: str, fetcher: Callable[[], T], skip_cache: bool = False) -> T:
    """Single-flight TTL cache helper. Double-checked locking: concurrent callers
    on the same key serialize on a per-key lock, and all but the leader find the
    value already cached on re-check."""
    if not skip_cache:
        cached = _cache_get(key)
        if cached is not None:
            return cached  # type: ignore[return-value]

    lock = _get_keyed_lock(key)
    with lock:
        if not skip_cache:
            cached = _cache_get(key)
            if cached is not None:
                return cached  # type: ignore[return-value]
        result = fetcher()
        _cache_set(key, result)
        return result


def invalidate_cache(prefix: str = "") -> None:
    """Clear all cache entries, or only those matching a prefix."""
    with _cache_lock:
        if not prefix:
            _cache.clear()
        else:
            for k in list(_cache.keys()):
                if k.startswith(prefix):
                    del _cache[k]


def evict_cache_entry(key: str) -> bool:
    """Evict a specific cache entry by key. Returns True if found and evicted."""
    with _cache_lock:
        if key in _cache:
            del _cache[key]
            return True
        return False


def get_cache_snapshot() -> tuple[list[tuple[str, float, float, int]], int, list[str]]:
    """Return cache metadata snapshot for admin dashboard.
    Returns: [(key, created, last_accessed, size_bytes), ...], total_bytes, inflight_keys"""
    with _cache_lock:
        entries = [
            (k, e.created_at, e.last_accessed, e.size_bytes)
            for k, e in _cache.items()
        ]
        total_bytes = _cache.currsize
    with _keyed_locks_guard:
        inflight_keys = [k for k, lock in _keyed_locks.items() if lock.locked()]
    return entries, total_bytes, inflight_keys

# ---------------------------------------------------------------------------
# Per-entity cost cache — refreshed in background, read O(1) from the hot path.
#
# Cost is computed by JOINing system.billing.usage to system.billing.list_prices
# on (sku_name, usage_unit, usage_start_time ∈ [price_start_time, price_end_time)),
# so every serverless SKU is priced correctly across regions and tiers (16+
# JOBS_SERVERLESS SKUs at $0.20–$0.59/DBU). The single global aggregation can
# take 1–4 min in busy workspaces, so it runs in the background only — the
# lineage request reads from a dict and never executes SQL for cost.
#
# Job SKU filter: '%JOBS_SERVERLESS%' only. Classic-compute jobs are
# intentionally excluded — interactive clusters can run many jobs concurrently
# and attribution rules differ (divide-by-N, percentile, etc.), so we don't
# guess. DLT pipelines are unfiltered: DLT compute is dedicated per pipeline.
# ---------------------------------------------------------------------------
_cost_by_job_id: dict[str, float] = {}
_cost_by_pipeline_id: dict[str, float] = {}
_cost_cache_fetched_at: float = 0.0
_cost_cache_lock = threading.Lock()
# system.billing rolls up at most daily, and the account-wide aggregation is the
# single most expensive system query (1–4 min on large accounts). Refreshing it
# hourly just burns warehouse time for no fresher data — default to 6h, tunable.
_COST_CACHE_TTL = int(os.environ.get("COST_CACHE_TTL_SECONDS", "21600"))  # 6 hours
_COST_WINDOW_DAYS = int(os.environ.get("COST_WINDOW_DAYS", "30"))         # cost window per entity
_COST_REFRESH_BUDGET_S = 600    # max time for one background refresh


def _execute_sql_long(client: WorkspaceClient, sql: str, max_wait_s: int) -> list[dict]:
    """Like _execute_sql but polls past the 50s wait_timeout cap of the API.
    For background refresh of expensive system.billing queries only — never
    call from the lineage hot path."""
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if not warehouse_id:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=warehouse_id, wait_timeout="50s",
    )
    deadline = time.time() + max_wait_s
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        if time.time() > deadline:
            raise RuntimeError(f"SQL exceeded {max_wait_s}s budget: statement_id={resp.statement_id}")
        time.sleep(5)
        resp = client.statement_execution.get_statement(resp.statement_id)
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [col.name for col in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def _refresh_cost_cache(client: WorkspaceClient) -> None:
    """Refresh per-job and per-pipeline cost dicts from system.billing.
    Single global aggregation; safe to call from multiple threads (non-blocking
    lock — concurrent callers no-op rather than queue)."""
    global _cost_by_job_id, _cost_by_pipeline_id, _cost_cache_fetched_at
    if not _cost_cache_lock.acquire(blocking=False):
        return
    try:
        job_sql = f"""
        SELECT u.usage_metadata.job_id AS id,
               ROUND(SUM(u.usage_quantity * lp.pricing.effective_list.default), 2) AS cost_usd
        FROM system.billing.usage u
        JOIN system.billing.list_prices lp
          ON u.sku_name = lp.sku_name
         AND u.usage_unit = lp.usage_unit
         AND u.usage_start_time >= lp.price_start_time
         AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
        WHERE u.usage_metadata.job_id IS NOT NULL
          AND u.sku_name LIKE '%JOBS_SERVERLESS%'
          AND u.usage_date > current_date() - INTERVAL {_COST_WINDOW_DAYS} DAYS
        GROUP BY u.usage_metadata.job_id
        """
        pipeline_sql = f"""
        SELECT u.usage_metadata.dlt_pipeline_id AS id,
               ROUND(SUM(u.usage_quantity * lp.pricing.effective_list.default), 2) AS cost_usd
        FROM system.billing.usage u
        JOIN system.billing.list_prices lp
          ON u.sku_name = lp.sku_name
         AND u.usage_unit = lp.usage_unit
         AND u.usage_start_time >= lp.price_start_time
         AND (lp.price_end_time IS NULL OR u.usage_start_time < lp.price_end_time)
        WHERE u.usage_metadata.dlt_pipeline_id IS NOT NULL
          AND u.usage_date > current_date() - INTERVAL {_COST_WINDOW_DAYS} DAYS
        GROUP BY u.usage_metadata.dlt_pipeline_id
        """
        try:
            jobs = {
                str(r["id"]): float(r["cost_usd"])
                for r in _execute_sql_long(client, job_sql, _COST_REFRESH_BUDGET_S)
            }
        except Exception as e:
            logger.warning(f"Job cost refresh failed (need SELECT on system.billing): {e}")
            return
        try:
            pipes = {
                str(r["id"]): float(r["cost_usd"])
                for r in _execute_sql_long(client, pipeline_sql, _COST_REFRESH_BUDGET_S)
            }
        except Exception as e:
            logger.warning(f"Pipeline cost refresh failed: {e}")
            return
        _cost_by_job_id = jobs
        _cost_by_pipeline_id = pipes
        _cost_cache_fetched_at = time.time()
        logger.info(
            f"Cost cache refreshed: {len(jobs)} jobs (serverless), "
            f"{len(pipes)} pipelines, {_COST_WINDOW_DAYS}d window"
        )
    finally:
        _cost_cache_lock.release()


def _execute_sql(client: WorkspaceClient, sql: str, catalog: str = None) -> list[dict]:
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
    if not warehouse_id:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")

    resp = client.statement_execution.execute_statement(
        statement=sql,
        warehouse_id=warehouse_id,
        catalog=catalog,
        wait_timeout=SQL_WAIT_TIMEOUT,
    )

    if resp.status.state == StatementState.FAILED:
        raise RuntimeError(f"SQL failed: {resp.status.error.message if resp.status.error else 'Unknown error'}")

    if resp.status.state != StatementState.SUCCEEDED:
        raise RuntimeError(f"SQL did not complete: {resp.status.state}")

    if not resp.result or not resp.result.data_array:
        return []

    columns = [col.name for col in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


def list_catalogs() -> list[str]:
    """List catalogs via SHOW CATALOGS SQL. Coalesced + cached."""
    cache_key = "catalogs"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _fetch() -> list[str]:
        client = _get_client()
        skip = {"system", "__databricks_internal"}
        try:
            rows = _execute_sql(client, "SHOW CATALOGS")
            return sorted([r["catalog"] for r in rows if r["catalog"] not in skip])
        except Exception as e:
            logger.error(f"SHOW CATALOGS failed: {e}")
            return []

    # Empty results bypass caching (same as before): retry on next call.
    lock = _get_keyed_lock(cache_key)
    with lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        result = _fetch()
        if result:
            _cache_set(cache_key, result)
        return result


def list_all_tables() -> list[dict]:
    """List all tables across all accessible catalogs via SQL.

    Uses per-catalog information_schema.tables — no UC SDK listing calls.
    The SDK's tables.list() has the include_browse issue: it silently
    returns empty for BROWSE-only access. SQL is authoritative.
    Cached with TTL/LRU + request coalescing. Never caches empty results.
    """
    cache_key = "all_tables"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _fetch() -> list[dict]:
        client = _get_client()
        catalogs = list_catalogs()
        tables: list[dict] = []
        for cat in catalogs:
            try:
                sql = f"""
                SELECT table_name, table_type, table_schema
                FROM `{cat}`.information_schema.tables
                WHERE table_schema NOT IN ('information_schema', 'default')
                  AND {_INTERNAL_TABLE_FILTER}
                ORDER BY table_schema, table_name
                """
                rows = _execute_sql(client, sql, catalog=cat)
                for r in rows:
                    sch = r["table_schema"]
                    name = r["table_name"]
                    tables.append({
                        "name": name,
                        "fqdn": f"{cat}.{sch}.{name}",
                        "catalog": cat,
                        "schema": sch,
                        "table_type": r["table_type"] or "TABLE",
                    })
            except Exception as e:
                logger.warning(f"Failed to list tables in catalog {cat}: {e}")
                continue
        return tables

    # Never cache empty results (retry on next call).
    lock = _get_keyed_lock(cache_key)
    with lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        tables = _fetch()
        if tables:
            _cache_set(cache_key, tables)
        return tables


def list_schemas(catalog: str) -> list[str]:
    """List schemas via SHOW SCHEMAS SQL. Coalesced + cached."""
    cache_key = f"schemas:{catalog}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    def _fetch() -> list[str]:
        client = _get_client()
        skip = {"information_schema", "default"}
        try:
            rows = _execute_sql(client, f"SHOW SCHEMAS IN `{catalog}`", catalog=catalog)
            return sorted([r["databaseName"] for r in rows if r["databaseName"] not in skip])
        except Exception as e:
            logger.error(f"SHOW SCHEMAS failed for {catalog}: {e}")
            return []

    lock = _get_keyed_lock(cache_key)
    with lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        result = _fetch()
        if result:
            _cache_set(cache_key, result)
        return result


def _wrap_with_cache_metadata(
    result: LineageResponse,
    cache_key: str,
    from_cache: bool,
    fetch_ms: int | None = None,
) -> LineageResponse:
    """Return a copy of the response with cache metadata attached.

    Uses model_copy so the cached object stays immutable — concurrent requests
    can't observe a half-updated response, and there's no shared-reference drift
    between what's in the cache and what goes out on the wire.
    """
    updates: dict = {"lineage_window_days": LINEAGE_WINDOW_DAYS}
    cache_ts = _cache_get_ts(cache_key)
    if cache_ts is not None:
        updates["cached"] = from_cache
        updates["cached_at"] = datetime.fromtimestamp(cache_ts, tz=timezone.utc).isoformat()
        updates["cache_expires_at"] = datetime.fromtimestamp(
            cache_ts + CACHE_TTL_SECONDS, tz=timezone.utc
        ).isoformat()
    if fetch_ms is not None:
        updates["fetch_duration_ms"] = fetch_ms
    return result.model_copy(update=updates) if updates else result


def get_table_lineage(catalog: str, schema: str | None = None, skip_cache: bool = False) -> LineageResponse:
    """Build a lineage graph for one schema, or for an entire catalog when
    schema is None. Catalog-wide graphs span every accessible schema in the
    catalog and can be large — see LINEAGE_MAX_NODES for the safety cap."""
    cache_key = f"lineage:{catalog}.{schema}" if schema else f"lineage:{catalog}"

    if not skip_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return _wrap_with_cache_metadata(cached, cache_key, from_cache=True, fetch_ms=0)

    lock = _get_keyed_lock(cache_key)
    with lock:
        if not skip_cache:
            cached = _cache_get(cache_key)
            if cached is not None:
                return _wrap_with_cache_metadata(cached, cache_key, from_cache=True, fetch_ms=0)

        fetch_start = time.time()
        result, lineage_ok = _fetch_table_lineage(catalog, schema, cache_key)
        fetch_ms = int((time.time() - fetch_start) * 1000)
        if lineage_ok:
            _cache_set(cache_key, result)
        return _wrap_with_cache_metadata(result, cache_key, from_cache=False, fetch_ms=fetch_ms)


def get_lineage_trace(seed_full_name: str, skip_cache: bool = False) -> LineageResponse:
    """End-to-end lineage trace from a single seed table, ACROSS all catalogs.

    Unlike get_table_lineage (scoped to one schema/catalog with 1-hop stubs for
    cross-scope neighbors), this walks system.access.table_lineage breadth-first
    from the seed in both directions, following edges into any catalog/schema,
    and returns only the connected component. This is what surfaces a full
    cross-catalog medallion chain (e.g. shared source → bronze → silver → gold in
    another catalog → mart) — with the mediating pipeline/job entity nodes — from
    a single clicked table. Metastore-wide; no workspace/catalog scoping.
    """
    cache_key = f"trace:{seed_full_name}"
    if not skip_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            return _wrap_with_cache_metadata(cached, cache_key, from_cache=True, fetch_ms=0)

    lock = _get_keyed_lock(cache_key)
    with lock:
        if not skip_cache:
            cached = _cache_get(cache_key)
            if cached is not None:
                return _wrap_with_cache_metadata(cached, cache_key, from_cache=True, fetch_ms=0)
        fetch_start = time.time()
        result = _fetch_lineage_trace(seed_full_name)
        fetch_ms = int((time.time() - fetch_start) * 1000)
        _cache_set(cache_key, result)
        return _wrap_with_cache_metadata(result, cache_key, from_cache=False, fetch_ms=fetch_ms)


def _fetch_lineage_trace(seed_full_name: str) -> LineageResponse:
    """DIRECTIONAL BFS over system.access.table_lineage from the seed, across all
    catalogs. Ancestors are found by following target→source only; descendants by
    source→target only. This is essential: a shared source table (e.g.
    samples.tpch.orders) is read by many unrelated pipelines account-wide, so a
    bidirectional walk would fan out through it into every sibling consumer. By
    keeping each direction pure, the trace stays within the seed's own lineage
    cone (its true ancestors + descendants)."""
    client = _get_client()

    MAX_ITERS = 16
    NODE_CAP = LINEAGE_MAX_NODES
    lineage_rows: list[dict] = []
    row_keys: set[tuple] = set()
    seen_all: set[str] = {seed_full_name}
    truncated = {"hit": False}

    def _collect(rows: list[dict]):
        for r in rows:
            k = (r.get("source_table_full_name"), r.get("target_table_full_name"),
                 r.get("source_path"), r.get("target_path"), r.get("entity_id"))
            if k not in row_keys:
                row_keys.add(k)
                lineage_rows.append(r)

    def _walk(direction: str):
        # direction "up": match target IN frontier, expand by sources (ancestors).
        # direction "down": match source IN frontier, expand by targets (descendants).
        match_col = "target_table_full_name" if direction == "up" else "source_table_full_name"
        next_col = "source_table_full_name" if direction == "up" else "target_table_full_name"
        frontier = {seed_full_name}
        for _ in range(MAX_ITERS):
            if len(seen_all) > NODE_CAP:
                truncated["hit"] = True
                break
            if not frontier:
                break
            in_list = ",".join("'" + t.replace("'", "''") + "'" for t in frontier)
            sql = f"""
            SELECT source_table_full_name, target_table_full_name, source_type, target_type,
                   source_path, target_path, entity_type, entity_id, event_time, created_by
            FROM system.access.table_lineage
            WHERE {match_col} IN ({in_list})
              AND event_time > current_date() - INTERVAL {LINEAGE_WINDOW_DAYS} DAYS
              AND {_internal_lineage_filter()}
            """
            try:
                rows = _execute_sql(client, sql)
            except Exception as e:
                logger.warning(f"Trace query failed (need SELECT on system.access): {e}")
                break
            _collect(rows)
            nxt: set[str] = set()
            for r in rows:
                fn = r.get(next_col)
                if fn and fn not in seen_all:
                    seen_all.add(fn)
                    nxt.add(fn)
            frontier = nxt
        # Frontier still non-empty after MAX_ITERS hops → the chain is deeper than
        # we walked; the graph is partial. Surface it (don't present it as complete).
        if frontier:
            truncated["hit"] = True

    _walk("up")
    _walk("down")
    return _build_graph_from_rows(client, lineage_rows, truncated=truncated["hit"])


# --- Shared graph-assembly helpers (used by BOTH the trace builder and the
# schema/catalog builder, so the two can't drift) ---------------------------

def _entity_cost(entity_type: str, entity_id: str) -> float | None:
    """30-day serverless cost for a JOB/PIPELINE entity from the cost cache."""
    if entity_type == "JOB":
        return _cost_by_job_id.get(entity_id)
    if entity_type == "PIPELINE":
        return _cost_by_pipeline_id.get(entity_id)
    return None


def _maybe_refresh_cost_cache(client: WorkspaceClient) -> None:
    """Kick a background cost refresh if the cache is stale. Non-blocking; the
    refresh's own non-blocking lock means concurrent callers no-op rather than pile up."""
    if (time.time() - _cost_cache_fetched_at) >= _COST_CACHE_TTL:
        threading.Thread(target=_refresh_cost_cache, args=(client,), daemon=True).start()


def _classify_table_nodes(nodes_map: dict, edge_set: set) -> None:
    """Set upstream/downstream counts + lineage_status on every TableNode in place.
    Entity (job/pipeline) intermediaries don't count toward table connectivity."""
    table_ids = {nid for nid, n in nodes_map.items() if isinstance(n, TableNode)}
    up: dict[str, int] = {}
    down: dict[str, int] = {}
    for s, t in edge_set:
        if s in table_ids:
            down[s] = down.get(s, 0) + 1
        if t in table_ids:
            up[t] = up.get(t, 0) + 1
    for nid, node in nodes_map.items():
        if not isinstance(node, TableNode):
            continue
        node.upstream_count = up.get(nid, 0)
        node.downstream_count = down.get(nid, 0)
        if node.upstream_count == 0 and node.downstream_count == 0:
            node.lineage_status = "orphan"
        elif node.upstream_count == 0:
            node.lineage_status = "root"
        elif node.downstream_count == 0:
            node.lineage_status = "leaf"
        else:
            node.lineage_status = "connected"


def _build_graph_from_rows(client: WorkspaceClient, lineage_rows: list[dict], truncated: bool = False) -> LineageResponse:
    """Build a LineageResponse (table + entity nodes, routed edges, counts, cost)
    from a flat list of system.access.table_lineage rows. Shared by the trace path.
    Table nodes are lightweight (no columns/owner) — columns lazy-load via /api/columns."""
    nodes_map: dict[str, TableNode | EntityNode] = {}
    entity_map: dict[str, dict] = {}
    direct_pairs: set[tuple[str, str]] = set()

    def _ensure_table(ref: str, rtype: str | None):
        if ref in nodes_map:
            return
        if rtype == "PATH":
            raw = ref.removeprefix("path:")
            name = raw.split("://", 1)[-1].split("/")[0] if "://" in raw else raw
        else:
            name = ref.split(".")[-1]
        nodes_map[ref] = TableNode(
            id=ref, name=name, full_name=ref,
            table_type=rtype or "TABLE", owner=None, comment=None,
            columns=[], created_at=None, updated_at=None,
        )

    for r in lineage_rows:
        sref, stype = _parse_lineage_ref(r.get("source_table_full_name"), r.get("source_path"), r.get("source_type"))
        tref, ttype = _parse_lineage_ref(r.get("target_table_full_name"), r.get("target_path"), r.get("target_type"))
        if sref:
            _ensure_table(sref, stype)
        if tref:
            _ensure_table(tref, ttype)
        etype, eid = r.get("entity_type"), r.get("entity_id")
        if etype and eid:
            key = f"entity:{etype}:{eid}"
            info = entity_map.setdefault(key, {"type": etype, "id": eid, "sources": set(), "targets": set(), "last_run": None, "owner": r.get("created_by")})
            if sref:
                info["sources"].add(sref)
            if tref:
                info["targets"].add(tref)
            et = r.get("event_time")
            if et and (info["last_run"] is None or str(et) > str(info["last_run"])):
                info["last_run"] = et
        elif sref and tref:
            direct_pairs.add((sref, tref))

    # Populate columns (batched, one query per catalog) so table nodes are
    # expandable for column lineage. Lightweight nodes start with columns=[];
    # special catalogs without information_schema (e.g. samples) just stay empty.
    tables_by_cat: dict[str, set[tuple[str, str]]] = {}
    for ref, node in nodes_map.items():
        if isinstance(node, TableNode) and node.table_type not in ("VOLUME", "PATH"):
            parts = ref.split(".")
            if len(parts) == 3:
                tables_by_cat.setdefault(parts[0], set()).add((parts[1], parts[2]))
    for cat, pairs in tables_by_cat.items():
        sch_in = ",".join("'" + s.replace("'", "''") + "'" for s in {s for s, _ in pairs})
        tbl_in = ",".join("'" + t.replace("'", "''") + "'" for t in {t for _, t in pairs})
        sql = f"""
        SELECT table_schema, table_name, column_name, data_type, is_nullable, ordinal_position
        FROM `{cat}`.information_schema.columns
        WHERE table_schema IN ({sch_in}) AND table_name IN ({tbl_in})
        ORDER BY table_schema, table_name, ordinal_position
        """
        try:
            crows = _execute_sql(client, sql, catalog=cat)
        except Exception as e:
            logger.warning(f"Trace column fetch failed for catalog {cat}: {e}")
            continue
        cols_by_t: dict[tuple[str, str], list[dict]] = {}
        for c in crows:
            cols_by_t.setdefault((c["table_schema"], c["table_name"]), []).append({
                "name": c["column_name"], "type": c["data_type"], "nullable": c["is_nullable"] == "YES",
            })
        for (sch, tname), cols in cols_by_t.items():
            n = nodes_map.get(f"{cat}.{sch}.{tname}")
            if isinstance(n, TableNode):
                n.columns = cols
            _cache_set(f"columns:{cat}.{sch}.{tname}", cols)

    for key, info in entity_map.items():
        nodes_map[key] = EntityNode(id=key, entity_type=info["type"], entity_id=info["id"],
                                    last_run=info["last_run"], owner=info["owner"])
        c = _entity_cost(info["type"], info["id"])
        if c is not None:
            nodes_map[key].cost_usd = c

    _maybe_refresh_cost_cache(client)

    # Build edges, breaking the FALSE self-cycle that arises when one pipeline
    # writes a table and then reads it back to build another output (e.g. the gold
    # pipeline writes gold_customer_revenue, then reads it to build
    # gold_region_summary). Routing both through the single entity node would add
    # entity→T and T→entity. Fix: a table the entity WRITES is never also drawn as
    # feeding that entity — its read-after-write is shown as a direct table→table
    # edge instead. The result stays acyclic.
    targets_by_entity: dict[str, set[str]] = {
        key: set(info["targets"]) for key, info in entity_map.items()
    }
    edge_set: set[tuple[str, str]] = set()
    for r in lineage_rows:
        sref, _ = _parse_lineage_ref(r.get("source_table_full_name"), r.get("source_path"), r.get("source_type"))
        tref, _ = _parse_lineage_ref(r.get("target_table_full_name"), r.get("target_path"), r.get("target_type"))
        etype, eid = r.get("entity_type"), r.get("entity_id")
        if etype and eid:
            key = f"entity:{etype}:{eid}"
            if tref:
                edge_set.add((key, tref))
            if sref:
                if sref in targets_by_entity.get(key, set()):
                    # internal read-after-write → direct edge, no back-edge to entity
                    if tref:
                        edge_set.add((sref, tref))
                else:
                    edge_set.add((sref, key))
        elif sref and tref:
            edge_set.add((sref, tref))

    _classify_table_nodes(nodes_map, edge_set)

    return LineageResponse(nodes=list(nodes_map.values()),
                           edges=[LineageEdge(source=s, target=t) for s, t in edge_set],
                           truncated=truncated)


def _parse_lineage_ref(table_full_name: str | None, path: str | None, ref_type: str | None) -> tuple[str | None, str | None]:
    """Parse a lineage source/target into (node_id, node_type).

    Returns a stable node ID and a type string suitable for TableNode.table_type.
    Handles tables, views, streaming tables, volumes (/Volumes/...), and cloud paths (s3://).
    """
    if table_full_name:
        # Map system lineage types to display types
        type_map = {
            "TABLE": "TABLE",
            "VIEW": "VIEW",
            "MATERIALIZED_VIEW": "MATERIALIZED_VIEW",
            "STREAMING_TABLE": "STREAMING_TABLE",
        }
        return table_full_name, type_map.get(ref_type, ref_type or "TABLE")

    if path:
        # Volume path: /Volumes/catalog/schema/volume_name/...
        if path.startswith("/Volumes/"):
            parts = path.split("/")
            if len(parts) >= 5:
                vol_id = f"{parts[2]}.{parts[3]}.{parts[4]}"
                return vol_id, "VOLUME"
            return f"volume:{path}", "VOLUME"

        # Cloud storage path: s3://bucket/..., abfss://container@account/...
        if "://" in path:
            proto, rest = path.split("://", 1)
            bucket = rest.split("/")[0]
            return f"path:{proto}://{bucket}", "PATH"

        # Other path
        return f"path:{path[:80]}", "PATH"

    return None, None


def _fetch_table_lineage(catalog: str, schema: str | None, cache_key: str) -> tuple[LineageResponse, bool]:
    """Actual DBSQL fetch — called by at most one thread per cache key at a time.

    When schema is None the graph spans the entire catalog (every accessible
    schema). Catalog-wide tables can collide on bare table_name across schemas,
    so columns and lookups are keyed by (schema, table_name), never table_name
    alone.
    """
    client = _get_client()

    # Schema predicate shared by the tables + columns queries. Catalog-wide
    # excludes the noise schemas; schema-scoped pins to the one schema.
    if schema is not None:
        schema_filter = f"table_schema = '{schema}'"
    else:
        schema_filter = "table_schema NOT IN ('information_schema', 'default')"

    # Get all tables/views in scope
    tables_sql = f"""
    SELECT
        table_schema,
        table_name,
        table_type,
        table_owner,
        comment,
        created,
        last_altered
    FROM `{catalog}`.information_schema.tables
    WHERE {schema_filter}
      AND {_INTERNAL_TABLE_FILTER}
    ORDER BY table_schema, table_name
    """
    table_rows = _execute_sql(client, tables_sql, catalog=catalog)

    # Guard: refuse catalog-wide graphs that are too large to lay out / cache.
    if schema is None and len(table_rows) > LINEAGE_MAX_NODES:
        raise RuntimeError(
            f"Catalog '{catalog}' has {len(table_rows)} tables, exceeding the "
            f"{LINEAGE_MAX_NODES}-table limit for catalog-wide lineage. "
            f"Explore by schema instead."
        )

    # Get columns for all tables in scope
    columns_sql = f"""
    SELECT
        table_schema,
        table_name,
        column_name,
        data_type,
        is_nullable,
        ordinal_position
    FROM `{catalog}`.information_schema.columns
    WHERE {schema_filter}
    ORDER BY table_schema, table_name, ordinal_position
    """
    column_rows = _execute_sql(client, columns_sql, catalog=catalog)

    # Group columns by (schema, table_name) — bare table_name collides across
    # schemas in catalog-wide mode.
    columns_by_table: dict[tuple[str, str], list[dict]] = {}
    for col in column_rows:
        key = (col["table_schema"], col["table_name"])
        if key not in columns_by_table:
            columns_by_table[key] = []
        columns_by_table[key].append({
            "name": col["column_name"],
            "type": col["data_type"],
            "nullable": col["is_nullable"] == "YES",
        })

    # Cache columns separately for the lazy /api/columns endpoint
    for (sch, tname), cols in columns_by_table.items():
        _cache_set(f"columns:{catalog}.{sch}.{tname}", cols)

    # Pre-build in-scope tables set for lineage filtering
    schema_tables = set()
    for t in table_rows:
        schema_tables.add(f"{catalog}.{t['table_schema']}.{t['table_name']}")

    # Get lineage edges from system tables (with entity info for pipeline nodes).
    # Includes PATH entries (volumes, cloud storage) alongside table references.
    if schema is not None:
        lineage_scope_filter = f"""
        (target_table_catalog = '{catalog}' AND target_table_schema = '{schema}')
        OR
        (source_table_catalog = '{catalog}' AND source_table_schema = '{schema}')
        OR
        (source_path LIKE '/Volumes/{catalog}/{schema}/%')
        OR
        (target_path LIKE '/Volumes/{catalog}/{schema}/%')
        """
    else:
        lineage_scope_filter = f"""
        (target_table_catalog = '{catalog}')
        OR
        (source_table_catalog = '{catalog}')
        OR
        (source_path LIKE '/Volumes/{catalog}/%')
        OR
        (target_path LIKE '/Volumes/{catalog}/%')
        """
    lineage_sql = f"""
    SELECT
        source_table_full_name,
        target_table_full_name,
        source_type,
        target_type,
        source_path,
        target_path,
        entity_type,
        entity_id,
        event_time,
        created_by
    FROM system.access.table_lineage
    WHERE (
        {lineage_scope_filter}
    )
    AND event_time > current_date() - INTERVAL {LINEAGE_WINDOW_DAYS} DAYS
    AND {_internal_lineage_filter()}
    """
    lineage_ok = True
    try:
        lineage_rows = _execute_sql(client, lineage_sql)
    except Exception as e:
        logger.warning(f"System lineage table query failed (ensure SELECT on system.access is granted): {e}")
        lineage_rows = []
        lineage_ok = False  # caller must NOT cache — a transient blip would freeze an empty graph for the whole TTL

    # Build table node map
    nodes_map: dict[str, TableNode | EntityNode] = {}
    for t in table_rows:
        sch = t["table_schema"]
        table_id = f"{catalog}.{sch}.{t['table_name']}"
        nodes_map[table_id] = TableNode(
            id=table_id,
            name=t["table_name"],
            full_name=table_id,
            table_type=t["table_type"] or "TABLE",
            owner=t.get("table_owner"),
            comment=t.get("comment"),
            columns=columns_by_table.get((sch, t["table_name"]), []),
            created_at=t.get("created"),
            updated_at=t.get("last_altered"),
        )

    # Group lineage rows by entity and build entity nodes + routed edges.
    # Two-pass approach: collect ALL entity rows first (without per-row local
    # filtering), then prune entities that don't touch any local table. This
    # ensures upstream (left-side) tables from other schemas are included when
    # the entity also writes to a local table.
    entity_map: dict[str, dict] = {}  # entity_key → {type, id, sources, targets, last_run, owner}
    direct_pairs: set[tuple[str, str]] = set()  # (src, tgt) for rows with no entity

    # Track external nodes (cross-schema tables, volumes, paths) that need stub nodes
    external_tables: set[str] = set()
    # Track the type of each node for proper rendering (VOLUME, PATH, STREAMING_TABLE, etc.)
    external_node_types: dict[str, str] = {}  # node_id → display type

    for row in lineage_rows:
        # Parse source and target using _parse_lineage_ref which handles
        # tables, volumes (/Volumes/...), and cloud paths (s3://...)
        src, src_type = _parse_lineage_ref(
            row.get("source_table_full_name"),
            row.get("source_path"),
            row.get("source_type"),
        )
        tgt, tgt_type = _parse_lineage_ref(
            row.get("target_table_full_name"),
            row.get("target_path"),
            row.get("target_type"),
        )
        etype = row.get("entity_type")
        eid = row.get("entity_id")

        # Track types for external nodes
        if src and src not in schema_tables and src_type:
            external_node_types[src] = src_type
        if tgt and tgt not in schema_tables and tgt_type:
            external_node_types[tgt] = tgt_type

        # Entity-mediated rows: collect ALL without filtering — pruned below
        if etype and eid:
            entity_key = f"entity:{etype}:{eid}"
            if entity_key not in entity_map:
                entity_map[entity_key] = {
                    "type": etype, "id": eid,
                    "sources": set(), "targets": set(),
                    "last_run": None, "owner": None,
                }
            if src:
                entity_map[entity_key]["sources"].add(src)
            if tgt:
                entity_map[entity_key]["targets"].add(tgt)
            evt = row.get("event_time")
            if evt and (entity_map[entity_key]["last_run"] is None or evt > entity_map[entity_key]["last_run"]):
                entity_map[entity_key]["last_run"] = evt
            owner = row.get("created_by")
            if owner:
                entity_map[entity_key]["owner"] = owner
            continue

        # Direct pair (no entity) — filter per-row: at least one side must be local
        src_local = src in schema_tables if src else False
        tgt_local = tgt in schema_tables if tgt else False
        if not src_local and not tgt_local:
            continue

        if src and not src_local:
            external_tables.add(src)
        if tgt and not tgt_local:
            external_tables.add(tgt)

        if src and tgt:
            direct_pairs.add((src, tgt))

    # Prune entities that don't touch any local table
    pruned_entity_map: dict[str, dict] = {}
    for entity_key, info in entity_map.items():
        touches_local = (
            any(s in schema_tables for s in info["sources"])
            or any(t in schema_tables for t in info["targets"])
        )
        if not touches_local:
            continue
        pruned_entity_map[entity_key] = info
    entity_map = pruned_entity_map

    # Follow-up query: fetch COMPLETE lineage for discovered entities.
    # The initial query only returns rows where source OR target is in our schema,
    # but an entity may write to tables in OTHER schemas (cross-schema targets).
    # Without this, pipelines that read from our schema but write elsewhere show
    # no outward edges.
    followup_rows: list[dict] = []
    if entity_map:
        entity_ids = [info["id"] for info in entity_map.values()]
        eid_list = ",".join(f"'{eid}'" for eid in entity_ids)
        followup_sql = f"""
        SELECT
            source_table_full_name,
            target_table_full_name,
            source_type,
            target_type,
            source_path,
            target_path,
            entity_type,
            entity_id,
            event_time,
            created_by
        FROM system.access.table_lineage
        WHERE entity_id IN ({eid_list})
        AND event_time > current_date() - INTERVAL {LINEAGE_WINDOW_DAYS} DAYS
        AND {_internal_lineage_filter()}
        """
        try:
            followup_rows = _execute_sql(client, followup_sql)
            for row in followup_rows:
                src, src_type = _parse_lineage_ref(
                    row.get("source_table_full_name"),
                    row.get("source_path"),
                    row.get("source_type"),
                )
                tgt, tgt_type = _parse_lineage_ref(
                    row.get("target_table_full_name"),
                    row.get("target_path"),
                    row.get("target_type"),
                )
                etype = row.get("entity_type")
                eid = row.get("entity_id")
                if not etype or not eid:
                    continue
                entity_key = f"entity:{etype}:{eid}"
                if entity_key not in entity_map:
                    continue  # skip entities that were pruned
                if src:
                    entity_map[entity_key]["sources"].add(src)
                    if src not in schema_tables and src_type:
                        external_node_types[src] = src_type
                if tgt:
                    entity_map[entity_key]["targets"].add(tgt)
                    if tgt not in schema_tables and tgt_type:
                        external_node_types[tgt] = tgt_type
                evt = row.get("event_time")
                if evt and (entity_map[entity_key]["last_run"] is None or evt > entity_map[entity_key]["last_run"]):
                    entity_map[entity_key]["last_run"] = evt
                owner = row.get("created_by")
                if owner:
                    entity_map[entity_key]["owner"] = owner
        except Exception as e:
            logger.warning(f"Entity follow-up lineage query failed: {e}")

    # Track external tables from all entity sources/targets
    for info in entity_map.values():
        for s in info["sources"]:
            if s not in schema_tables:
                external_tables.add(s)
        for t in info["targets"]:
            if t not in schema_tables:
                external_tables.add(t)

    # Create stub nodes for cross-schema/cross-catalog tables, volumes, and paths.
    # Group 3-part-name tables by catalog.schema for batch column fetching.
    # Skip column fetch for VOLUME and PATH types (they don't have information_schema).
    non_table_types = {"VOLUME", "PATH"}
    ext_schema_groups: dict[tuple[str, str], list[str]] = {}
    for ext_table in external_tables:
        node_type = external_node_types.get(ext_table, "TABLE")
        if node_type in non_table_types:
            continue  # no columns to fetch for volumes/paths
        parts = ext_table.split(".")
        if len(parts) == 3:
            key = (parts[0], parts[1])
            if key not in ext_schema_groups:
                ext_schema_groups[key] = []
            ext_schema_groups[key].append(parts[2])

    # Fetch column metadata for external tables from their information_schema
    ext_columns: dict[str, list[dict]] = {}  # table_fqdn → [{name, type, nullable}]
    for (ext_cat, ext_sch), table_names in ext_schema_groups.items():
        table_list = ",".join(f"'{t}'" for t in table_names)
        col_sql = f"""
        SELECT table_name, column_name, full_data_type, is_nullable
        FROM {ext_cat}.information_schema.columns
        WHERE table_schema = '{ext_sch}' AND table_name IN ({table_list})
        ORDER BY table_name, ordinal_position
        """
        try:
            col_rows = _execute_sql(client, col_sql)
            for cr in col_rows:
                fqdn = f"{ext_cat}.{ext_sch}.{cr['table_name']}"
                if fqdn not in ext_columns:
                    ext_columns[fqdn] = []
                ext_columns[fqdn].append({
                    "name": cr["column_name"],
                    "type": cr["full_data_type"],
                    "nullable": cr.get("is_nullable", "YES") == "YES",
                })
        except Exception as e:
            logger.warning(f"Failed to fetch columns for external tables in {ext_cat}.{ext_sch}: {e}")

    for ext_table in external_tables:
        if ext_table not in nodes_map:
            parts = ext_table.split(".")
            node_type = external_node_types.get(ext_table, "EXTERNAL_LINEAGE")

            # Determine display name and comment based on node type
            if node_type == "VOLUME":
                display_name = parts[-1] if parts else ext_table
                comment = f"Volume in {'.'.join(parts[:2]) if len(parts) >= 2 else 'external'}"
            elif node_type == "PATH":
                # Cloud storage: strip path: prefix for display
                raw = ext_table.removeprefix("path:")
                display_name = raw.split("://", 1)[-1].split("/")[0] if "://" in raw else raw
                comment = f"External storage: {raw}"
            else:
                display_name = parts[-1] if parts else ext_table
                comment = f"Cross-schema reference from {'.'.join(parts[:2]) if len(parts) >= 2 else 'external'}"

            nodes_map[ext_table] = TableNode(
                id=ext_table,
                name=display_name,
                full_name=ext_table,
                table_type=node_type,
                owner=None,
                comment=comment,
                columns=ext_columns.get(ext_table, []),
                created_at=None,
                updated_at=None,
            )

    # Create entity nodes
    for entity_key, info in entity_map.items():
        nodes_map[entity_key] = EntityNode(
            id=entity_key,
            entity_type=info["type"],
            entity_id=info["id"],
            last_run=info["last_run"],
            owner=info["owner"],
        )

    # Annotate JOB and PIPELINE nodes with cost from the pre-aggregated cache.
    # See _refresh_cost_cache — no SQL on this hot path. Classic-compute jobs
    # are intentionally unpriced (shared-cluster attribution is ambiguous).
    for entity_key, info in entity_map.items():
        c = _entity_cost(info["type"], info["id"])
        if c is not None:
            nodes_map[entity_key].cost_usd = c

    _maybe_refresh_cost_cache(client)

    # Build edges: routed through entity nodes + direct edges
    edge_set: set[tuple[str, str]] = set()

    for entity_key, info in entity_map.items():
        for src in info["sources"]:
            edge_set.add((src, entity_key))
        for tgt in info["targets"]:
            edge_set.add((entity_key, tgt))

    # Direct edges (no entity info — backward compat)
    # Only add if not already covered by an entity-routed path
    entity_covered = set()
    for info in entity_map.values():
        for src in info["sources"]:
            for tgt in info["targets"]:
                entity_covered.add((src, tgt))

    for src, tgt in direct_pairs:
        if (src, tgt) not in entity_covered:
            edge_set.add((src, tgt))

    # Break false self-cycles: a table the entity WRITES must not also be drawn as
    # feeding that entity. When a pipeline/job writes T then reads it back to build
    # another output U (common in multi-step pipelines — the source of the cycles
    # seen in lineage_stress_test/pipeline_demo), drop the (T, entity) back-edge and
    # add the accurate direct (T, U) edge from the lineage rows. Keeps it acyclic.
    for entity_key, info in entity_map.items():
        for t in (info["sources"] & info["targets"]):
            edge_set.discard((t, entity_key))
    for row in lineage_rows + followup_rows:
        et, eid = row.get("entity_type"), row.get("entity_id")
        if not (et and eid):
            continue
        info = entity_map.get(f"entity:{et}:{eid}")
        if not info:
            continue
        sref, _ = _parse_lineage_ref(row.get("source_table_full_name"), row.get("source_path"), row.get("source_type"))
        tref, _ = _parse_lineage_ref(row.get("target_table_full_name"), row.get("target_path"), row.get("target_type"))
        if sref and tref and sref in info["targets"]:
            edge_set.add((sref, tref))

    edges = [LineageEdge(source=s, target=t) for s, t in edge_set]

    # Counts/status across ALL table nodes — including cross-schema/cross-catalog
    # stubs — so an external source/target with a real edge isn't mis-flagged orphan.
    _classify_table_nodes(nodes_map, edge_set)

    result = LineageResponse(
        nodes=list(nodes_map.values()),
        edges=edges,
    )
    # Caching of the top-level lineage response is handled by get_table_lineage.
    # lineage_ok=False means the lineage query failed → graph is tables-only; don't cache it.
    return result, lineage_ok


def resolve_entity_name(entity_type: str, entity_id: str) -> dict:
    """Resolve an entity ID to display name + metadata via system tables.

    Successful lookups are cached for the standard TTL. Fallbacks
    (resolution failed, no row found) are NOT cached so a transient lookup
    error can't stick a bad name in the cache for 8 hours.
    """
    cache_key = f"entity_name:{entity_type}:{entity_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    fallback_name = f"{entity_type} {entity_id[:12]}"
    resolved = False
    result: dict = {"name": fallback_name}

    # Defense-in-depth: entity_id is interpolated into SQL below. The /api/entity-name
    # endpoint validates it, but other callers (e.g. the Excel export) pass ids straight
    # from lineage rows — validate here too so this function is injection-safe on its own.
    if not _SAFE_ENTITY_ID_RE.match(entity_id or ""):
        logger.warning(f"resolve_entity_name: rejecting unsafe entity_id for {entity_type}")
        return result

    client = _get_client()
    try:
        if entity_type == "JOB":
            rows = _execute_sql(client, f"""
                SELECT name, run_as_user_name, creator_user_name
                FROM system.lakeflow.jobs
                WHERE job_id = '{entity_id}'
                LIMIT 1
            """)
            if rows:
                r = rows[0]
                if r.get("name"):
                    result["name"] = r["name"]
                    resolved = True
                result["owner"] = r.get("run_as_user_name") or r.get("creator_user_name")
        elif entity_type == "PIPELINE":
            rows = _execute_sql(client, f"""
                SELECT name FROM system.lakeflow.pipelines
                WHERE pipeline_id = '{entity_id}'
                LIMIT 1
            """)
            if rows and rows[0].get("name"):
                result["name"] = rows[0]["name"]
                resolved = True
        elif entity_type == "NOTEBOOK":
            if "/" in entity_id:
                result["name"] = entity_id.split("/")[-1]
                resolved = True
            else:
                # Numeric workspace object ID — resolve via audit log
                nb_rows = _execute_sql(client, f"""
                    SELECT request_params['path'] AS path
                    FROM system.access.audit
                    WHERE request_params['notebookId'] = '{entity_id}'
                      AND request_params['path'] IS NOT NULL
                    LIMIT 1
                """)
                if nb_rows and nb_rows[0].get("path"):
                    result["name"] = nb_rows[0]["path"].rsplit("/", 1)[-1]
                    resolved = True
                else:
                    result["name"] = f"Notebook {entity_id[:12]}"
    except Exception as e:
        logger.warning(f"Failed to resolve {entity_type} {entity_id}: {e}")

    if resolved:
        _cache_set(cache_key, result)
    return result


def get_columns(catalog: str, schema: str, table: str, skip_cache: bool = False) -> list[dict]:
    """Lazy column loader — returns columns for a single table (cache-first, coalesced)."""
    cache_key = f"columns:{catalog}.{schema}.{table}"

    def _fetch() -> list[dict]:
        client = _get_client()
        sql = f"""
        SELECT column_name, data_type, is_nullable, ordinal_position
        FROM `{catalog}`.information_schema.columns
        WHERE table_schema = '{schema}' AND table_name = '{table}'
        ORDER BY ordinal_position
        """
        rows = _execute_sql(client, sql, catalog=catalog)
        return [
            {"name": r["column_name"], "type": r["data_type"], "nullable": r["is_nullable"] == "YES"}
            for r in rows
        ]

    return _cached_fetch(cache_key, _fetch, skip_cache=skip_cache)


def get_schema_column_lineage(catalog: str, schema: str, skip_cache: bool = False) -> ColumnLineageResponse:
    """All column lineage for a schema from system.access.column_lineage.

    Returns every column-level edge within the schema — cached once, shared
    across all column clicks. The frontend does transitive traversal on these
    real UC edges (not heuristic name matching).
    """
    cache_key = f"col_lineage:{catalog}.{schema}"

    def _fetch() -> ColumnLineageResponse:
        client = _get_client()
        rows: list[dict] = []
        try:
            sql = f"""
            SELECT DISTINCT
                source_table_full_name,
                source_column_name,
                target_table_full_name,
                target_column_name
            FROM system.access.column_lineage
            WHERE (
                (target_table_catalog = '{catalog}' AND target_table_schema = '{schema}')
                OR
                (source_table_catalog = '{catalog}' AND source_table_schema = '{schema}')
            )
            AND source_table_full_name IS NOT NULL
            AND target_table_full_name IS NOT NULL
            AND source_table_full_name != target_table_full_name
            AND source_column_name IS NOT NULL
            AND target_column_name IS NOT NULL
            AND event_time > current_date() - INTERVAL {LINEAGE_WINDOW_DAYS} DAYS
            LIMIT 50000
            """
            rows = _execute_sql(client, sql)
        except Exception as e:
            logger.warning(f"Schema column lineage query failed: {e}")

        edges = [
            ColumnLineageEdge(
                source_table=row["source_table_full_name"],
                source_column=row["source_column_name"],
                target_table=row["target_table_full_name"],
                target_column=row["target_column_name"],
            )
            for row in rows
        ]
        return ColumnLineageResponse(edges=edges)

    return _cached_fetch(cache_key, _fetch, skip_cache=skip_cache)


def get_column_lineage(catalog: str, schema: str, table: str, column: str, skip_cache: bool = False) -> ColumnLineageResponse:
    """Column lineage for a specific table+column. Delegates to schema-level cache."""
    all_edges = get_schema_column_lineage(catalog, schema, skip_cache)
    full_table = f"{catalog}.{schema}.{table}"
    filtered = [e for e in all_edges.edges
                if (e.source_table == full_table and e.source_column == column)
                or (e.target_table == full_table and e.target_column == column)]
    return ColumnLineageResponse(edges=filtered)


def get_table_edges(catalog: str, schema: str | None = None, skip_cache: bool = False) -> list[dict]:
    """Return the REAL recorded table→table lineage pairs for a scope.

    Each row of system.access.table_lineage is an actual (source_table,
    target_table) dependency captured from a query — so distinct pairs here are
    the true table-level edges, with the mediating entity. This is what the
    Excel export's Lineage sheet should use, instead of reconstructing edges by
    cross-producting a job's source set × target set (which fabricates pairs
    that never ran — e.g. a job reading {A,B} and writing {C,D} does not imply
    A→D). Omit schema for catalog-wide scope.
    """
    cache_key = f"table_edges:{catalog}.{schema}" if schema else f"table_edges:{catalog}"

    def _fetch() -> list[dict]:
        client = _get_client()
        if schema is not None:
            scope = (
                f"(target_table_catalog = '{catalog}' AND target_table_schema = '{schema}') "
                f"OR (source_table_catalog = '{catalog}' AND source_table_schema = '{schema}')"
            )
        else:
            scope = f"target_table_catalog = '{catalog}' OR source_table_catalog = '{catalog}'"
        sql = f"""
        SELECT DISTINCT
            source_table_full_name AS source,
            target_table_full_name AS target,
            entity_type,
            entity_id
        FROM system.access.table_lineage
        WHERE ({scope})
          AND source_table_full_name IS NOT NULL
          AND target_table_full_name IS NOT NULL
          AND source_table_full_name != target_table_full_name
          AND event_time > current_date() - INTERVAL {LINEAGE_WINDOW_DAYS} DAYS
          AND {_internal_lineage_filter()}
        LIMIT 100000
        """
        try:
            rows = _execute_sql(client, sql)
        except Exception as e:
            logger.warning(f"table_edges query failed (need SELECT on system.access): {e}")
            return []
        return [
            {
                "source": r["source"],
                "target": r["target"],
                "entity_type": r.get("entity_type"),
                "entity_id": r.get("entity_id"),
            }
            for r in rows
        ]

    return _cached_fetch(cache_key, _fetch, skip_cache=skip_cache)


# ---------------------------------------------------------------------------
# Delta Sharing overlay — sharing relationships layered onto a lineage graph.
#
# Data comes from system.information_schema sharing views (not lineage tables —
# UC lineage doesn't cross the metastore boundary). The frontend matches these
# against nodes already in the graph and draws badges + synthetic boundary
# nodes. We never raise on missing privileges: the SPN may lack visibility into
# some sharing views, in which case the overlay is simply empty.
# ---------------------------------------------------------------------------


def _fetch_share_recipient_map(client: WorkspaceClient) -> dict[str, list[str]]:
    """share_name -> [recipient_name, ...] for shares granted SELECT. Empty on error."""
    sql = """
    SELECT share_name, recipient_name
    FROM system.information_schema.share_recipient_privileges
    WHERE privilege_type = 'SELECT'
    """
    out: dict[str, list[str]] = {}
    try:
        for r in _execute_sql(client, sql):
            out.setdefault(r["share_name"], []).append(r["recipient_name"])
    except Exception as e:
        logger.warning(f"share_recipient_privileges query failed: {e}")
    return out


def get_sharing_overlay(catalog: str, schema: str | None, audience: str = "both",
                        skip_cache: bool = False) -> SharingOverlay:
    """Delta Sharing overlay for a lineage scope.

    audience: 'provider' (outbound only), 'recipient' (inbound only), or 'both'.
    Outbound = my tables published into a share (table_share_usage).
    Inbound  = local catalogs created from a Delta Share (catalog_provider_share_usage).
    """
    aud = audience if audience in ("provider", "recipient", "both") else "both"
    cache_key = f"sharing_overlay:{catalog}.{schema}:{aud}"

    def _fetch() -> SharingOverlay:
        client = _get_client()
        want_out = aud in ("provider", "both")
        want_in = aud in ("recipient", "both")
        any_view_read = False
        shared_out: list[SharedOutEntry] = []
        foreign_catalogs: list[ForeignCatalogEntry] = []

        # --- Outbound: tables in this scope that are published into shares ---
        if want_out:
            # Metastore-wide (no catalog/schema filter): table_share_usage is tiny
            # (only shared objects), and a cross-catalog trace needs shared-out
            # tables from ANY catalog, not just the focused one. The frontend
            # matches these against the nodes actually in the graph.
            sql = """
            SELECT catalog_name, schema_name, table_name, share_name,
                   shared_as_schema, shared_as_table, cdf_enabled
            FROM system.information_schema.table_share_usage
            """
            try:
                rows = _execute_sql(client, sql)
                any_view_read = True
                recip_map = _fetch_share_recipient_map(client) if rows else {}
                for r in rows:
                    fq = f"{r['catalog_name']}.{r['schema_name']}.{r['table_name']}"
                    alias = None
                    if r.get("shared_as_table"):
                        sa_schema = r.get("shared_as_schema") or r["schema_name"]
                        alias = f"{sa_schema}.{r['shared_as_table']}"
                    shared_out.append(SharedOutEntry(
                        full_name=fq,
                        share_name=r["share_name"],
                        recipients=sorted(recip_map.get(r["share_name"], [])),
                        shared_as=alias,
                        cdf_enabled=str(r.get("cdf_enabled")).lower() == "true",
                    ))
            except Exception as e:
                logger.warning(f"table_share_usage query failed: {e}")

        # --- Inbound: local catalogs created from a Delta Share (metastore-wide) ---
        if want_in:
            sql = """
            SELECT c.catalog_name, c.provider_name, c.share_name, p.cloud, p.region
            FROM system.information_schema.catalog_provider_share_usage c
            LEFT JOIN system.information_schema.providers p
              ON c.provider_name = p.provider_name
            """
            try:
                rows = _execute_sql(client, sql)
                any_view_read = True
                by_cat: dict[str, ForeignCatalogEntry] = {}
                for r in rows:
                    cat = r["catalog_name"]
                    entry = by_cat.get(cat)
                    if entry is None:
                        entry = ForeignCatalogEntry(
                            catalog_name=cat,
                            provider_name=r.get("provider_name") or "unknown",
                            cloud=r.get("cloud"),
                            region=r.get("region"),
                        )
                        by_cat[cat] = entry
                    if r.get("share_name") and r["share_name"] not in entry.share_names:
                        entry.share_names.append(r["share_name"])
                foreign_catalogs = sorted(by_cat.values(), key=lambda e: e.catalog_name)
            except Exception as e:
                logger.warning(f"catalog_provider_share_usage query failed: {e}")

        return SharingOverlay(
            audience=aud,
            shared_out=shared_out,
            foreign_catalogs=foreign_catalogs,
            available=any_view_read,
        )

    return _cached_fetch(cache_key, _fetch, skip_cache=skip_cache)


def get_sharing_overview(skip_cache: bool = False) -> dict:
    """Metastore-wide Delta Sharing inventory for the landing 'Sharing overview' card.

    Counts and lists of shares, recipients, providers, foreign catalogs, and the
    tables published into shares. Each query degrades to empty on missing privileges.
    """
    cache_key = "sharing_overview"

    def _fetch() -> dict:
        client = _get_client()

        def _rows(sql: str) -> list[dict]:
            try:
                return _execute_sql(client, sql)
            except Exception as e:
                logger.warning(f"sharing overview query failed: {e}")
                return []

        shares = _rows(
            "SELECT share_name, share_owner, comment, created_by "
            "FROM system.information_schema.shares ORDER BY share_name"
        )
        recipients = _rows(
            "SELECT recipient_name, authentication_type, recipient_owner, comment "
            "FROM system.information_schema.recipients ORDER BY recipient_name"
        )
        providers = _rows(
            "SELECT provider_name, cloud, region, comment "
            "FROM system.information_schema.providers ORDER BY provider_name"
        )
        shared_tables = _rows(
            "SELECT catalog_name, schema_name, table_name, share_name "
            "FROM system.information_schema.table_share_usage "
            "ORDER BY share_name, catalog_name, schema_name, table_name"
        )
        foreign = _rows(
            "SELECT catalog_name, provider_name, share_name "
            "FROM system.information_schema.catalog_provider_share_usage "
            "ORDER BY catalog_name"
        )

        # recipients-per-share + tables-per-share for the share list
        recip_map = _fetch_share_recipient_map(client)
        tables_per_share: dict[str, int] = {}
        for t in shared_tables:
            tables_per_share[t["share_name"]] = tables_per_share.get(t["share_name"], 0) + 1

        # foreign catalogs grouped (a catalog can map to many shares)
        fcat: dict[str, dict] = {}
        for r in foreign:
            cat = r["catalog_name"]
            d = fcat.setdefault(cat, {"catalog_name": cat, "provider_name": r.get("provider_name"), "share_names": []})
            if r.get("share_name"):
                d["share_names"].append(r["share_name"])

        return {
            "shares": [
                {
                    "share_name": s["share_name"],
                    "owner": s.get("share_owner"),
                    "comment": s.get("comment"),
                    "num_tables": tables_per_share.get(s["share_name"], 0),
                    "recipients": sorted(recip_map.get(s["share_name"], [])),
                }
                for s in shares
            ],
            "recipients": [
                {
                    "recipient_name": r["recipient_name"],
                    "authentication_type": r.get("authentication_type"),
                    "owner": r.get("recipient_owner"),
                    "comment": r.get("comment"),
                }
                for r in recipients
            ],
            "providers": [
                {
                    "provider_name": p["provider_name"],
                    "cloud": p.get("cloud"),
                    "region": p.get("region"),
                    "comment": p.get("comment"),
                }
                for p in providers
            ],
            "foreign_catalogs": sorted(fcat.values(), key=lambda d: d["catalog_name"]),
            "shared_tables": [
                {
                    "full_name": f"{t['catalog_name']}.{t['schema_name']}.{t['table_name']}",
                    "share_name": t["share_name"],
                }
                for t in shared_tables
            ],
            "totals": {
                "shares": len(shares),
                "recipients": len(recipients),
                "providers": len(providers),
                "foreign_catalogs": len(fcat),
                "shared_tables": len(shared_tables),
            },
        }

    return _cached_fetch(cache_key, _fetch, skip_cache=skip_cache)


