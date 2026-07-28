"""Per-table capability cache for BrickTrace (Table Lineage workspace).

The Impact, Root Cause, Governance, and Access panels each run relatively
expensive system-table scans. Their results change slowly, so we persist the
last-computed payload per (table, tab) in an app-owned Delta table and serve it
instantly on the next open. A refresh icon in each panel forces a live recompute;
admins can evict entries from the Admin dashboard.

Semantics differ from the generic ``distributed_cache`` (cache_service.py):
  * We ALWAYS return the stored payload regardless of age, together with a
    ``stale`` flag (true once older than TTL). The UI shows the cached data
    immediately plus a "cached Xh ago / may be stale" badge — never a blank
    wait. Only an explicit refresh or admin eviction replaces it.

Table schema (``{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.capability_cache``):
    table_fqn   STRING     catalog.schema.table the payload is for
    tab         STRING     one of impact | root_cause | governance | access
    value_json  STRING     JSON payload (max 256 KB)
    cached_at   TIMESTAMP  when it was computed
    cached_by   STRING     actor email that triggered the compute

Fallback: every method is best-effort and returns None/0/[] on error — the
panels fall back to a live fetch, so a cache outage is never user-visible.
"""
from __future__ import annotations

import os
import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
CAP_CACHE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.capability_cache"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

# How long before a cached payload is flagged stale (still served, just badged).
CAPABILITY_CACHE_TTL_SECONDS = int(os.environ.get("CAPABILITY_CACHE_TTL_SECONDS", str(24 * 3600)))

MAX_VALUE_BYTES = 256_000
VALID_TABS = ("impact", "root_cause", "governance", "access")

_instance: Optional["CapabilityCache"] = None
_instance_lock = threading.Lock()


def get_capability_cache() -> "CapabilityCache":
    """Return the module-level CapabilityCache singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = CapabilityCache()
    return _instance


def _q(v: str) -> str:
    """Single-quote-escape a value for inline SQL."""
    return (v or "").replace("'", "''")


def serve_or_compute(table_fqn: str, tab: str, compute, actor: str = "", refresh: bool = False) -> dict:
    """Shared read-through helper used by the 4 capability routes.

    * refresh=False: return the cached payload if present (with a `_cache` meta
      block carrying cached_at / stale / cached_by / from_cache=True). On a miss,
      compute live, store it, and return with from_cache=False.
    * refresh=True: always compute live, overwrite the cache, and return fresh.

    `compute` is a zero-arg callable returning the capability's normal dict. The
    returned dict is the payload with a `_cache` key added — never raises for the
    cache itself; a compute() exception propagates to the caller as usual.
    """
    cache = get_capability_cache()
    if not refresh:
        hit = cache.get(table_fqn, tab)
        if hit is not None and isinstance(hit.get("data"), dict):
            payload = dict(hit["data"])
            payload["_cache"] = {
                "from_cache": True,
                "cached_at": hit.get("cached_at"),
                "cached_by": hit.get("cached_by"),
                "stale": hit.get("stale", False),
            }
            return payload

    data = compute()
    # Only cache dict payloads (all 4 capabilities return dicts).
    if isinstance(data, dict):
        cache.set(table_fqn, tab, data, actor=actor)
        payload = dict(data)
        payload["_cache"] = {
            "from_cache": False,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "cached_by": actor or None,
            "stale": False,
        }
        return payload
    return data


class CapabilityCache:
    """Delta-table-backed per-table cache for the 4 capability tabs."""

    def __init__(self) -> None:
        self._ready = False
        self._init_lock = threading.Lock()

    def _sql(self, statement: str) -> list[dict]:
        if not WAREHOUSE_ID:
            raise RuntimeError("No SQL warehouse configured.")
        client = _get_client()
        resp = client.statement_execution.execute_statement(
            statement=statement,
            warehouse_id=WAREHOUSE_ID,
            wait_timeout=SQL_WAIT_TIMEOUT,
        )
        if resp.status.state != StatementState.SUCCEEDED:
            err = resp.status.error.message if resp.status.error else resp.status.state
            raise RuntimeError(f"Capability-cache SQL failed: {err}")
        if not resp.result or not resp.result.data_array:
            return []
        cols = [c.name for c in resp.manifest.schema.columns]
        return [dict(zip(cols, row)) for row in resp.result.data_array]

    def _ensure_table(self) -> None:
        if self._ready:
            return
        with self._init_lock:
            if self._ready:
                return
            try:
                self._sql(f"""CREATE TABLE IF NOT EXISTS {CAP_CACHE_TABLE} (
                    table_fqn  STRING NOT NULL,
                    tab        STRING NOT NULL,
                    value_json STRING,
                    cached_at  TIMESTAMP,
                    cached_by  STRING
                ) USING DELTA""")
                self._ready = True
                logger.info("CapabilityCache: table ready at %s", CAP_CACHE_TABLE)
            except Exception as e:
                logger.warning("CapabilityCache: could not ensure table: %s", e)

    # -- read -----------------------------------------------------------------

    def get(self, table_fqn: str, tab: str) -> Optional[dict]:
        """Return {"data", "cached_at", "cached_by", "stale"} for (table, tab),
        or None on a genuine miss / error. Payload is returned regardless of age;
        `stale` is True once older than the TTL."""
        try:
            self._ensure_table()
            rows = self._sql(f"""
                SELECT value_json, cached_at, cached_by,
                       (cached_at < current_timestamp() - INTERVAL {CAPABILITY_CACHE_TTL_SECONDS} SECONDS) AS stale
                FROM {CAP_CACHE_TABLE}
                WHERE table_fqn = '{_q(table_fqn)}' AND tab = '{_q(tab)}'
                LIMIT 1
            """)
            if not rows or not rows[0].get("value_json"):
                return None
            r = rows[0]
            return {
                "data": json.loads(r["value_json"]),
                "cached_at": str(r.get("cached_at")) if r.get("cached_at") else None,
                "cached_by": r.get("cached_by"),
                "stale": bool(r.get("stale") in (True, "true", "True", 1, "1")),
            }
        except Exception as e:
            logger.debug("CapabilityCache.get error (non-fatal): %s", e)
            return None

    # -- write ----------------------------------------------------------------

    def set(self, table_fqn: str, tab: str, value: Any, actor: str = "") -> bool:
        """Store (or replace) the payload for (table, tab). Returns True on success."""
        try:
            self._ensure_table()
            serialized = json.dumps(value, default=str)
            if len(serialized.encode()) > MAX_VALUE_BYTES:
                logger.debug("CapabilityCache.set skipped: payload too large for %s/%s", table_fqn, tab)
                return False
            safe_val = _q(serialized)
            now = datetime.now(timezone.utc).isoformat()
            self._sql(f"""
                MERGE INTO {CAP_CACHE_TABLE} t
                USING (SELECT '{_q(table_fqn)}' AS table_fqn, '{_q(tab)}' AS tab) s
                ON t.table_fqn = s.table_fqn AND t.tab = s.tab
                WHEN MATCHED THEN UPDATE SET
                    value_json = '{safe_val}',
                    cached_at  = TIMESTAMP '{now}',
                    cached_by  = '{_q(actor)}'
                WHEN NOT MATCHED THEN INSERT
                    (table_fqn, tab, value_json, cached_at, cached_by)
                VALUES ('{_q(table_fqn)}', '{_q(tab)}', '{safe_val}',
                        TIMESTAMP '{now}', '{_q(actor)}')
            """)
            return True
        except Exception as e:
            logger.debug("CapabilityCache.set error (non-fatal): %s", e)
            return False

    # -- eviction -------------------------------------------------------------

    def evict(self, table_fqn: str, tab: str) -> bool:
        """Evict one (table, tab) entry."""
        try:
            self._ensure_table()
            self._sql(
                f"DELETE FROM {CAP_CACHE_TABLE} "
                f"WHERE table_fqn = '{_q(table_fqn)}' AND tab = '{_q(tab)}'"
            )
            return True
        except Exception as e:
            logger.debug("CapabilityCache.evict error: %s", e)
            return False

    def evict_table(self, table_fqn: str) -> int:
        """Evict all tabs for one table. Returns count removed."""
        try:
            self._ensure_table()
            rows = self._sql(
                f"SELECT COUNT(*) AS cnt FROM {CAP_CACHE_TABLE} "
                f"WHERE table_fqn = '{_q(table_fqn)}'"
            )
            count = int((rows[0]["cnt"] or 0)) if rows else 0
            if count > 0:
                self._sql(f"DELETE FROM {CAP_CACHE_TABLE} WHERE table_fqn = '{_q(table_fqn)}'")
            return count
        except Exception as e:
            logger.debug("CapabilityCache.evict_table error: %s", e)
            return 0

    def evict_all(self) -> int:
        """Evict the entire capability cache. Returns count removed."""
        try:
            self._ensure_table()
            rows = self._sql(f"SELECT COUNT(*) AS cnt FROM {CAP_CACHE_TABLE}")
            count = int((rows[0]["cnt"] or 0)) if rows else 0
            if count > 0:
                self._sql(f"DELETE FROM {CAP_CACHE_TABLE} WHERE true")
            return count
        except Exception as e:
            logger.debug("CapabilityCache.evict_all error: %s", e)
            return 0

    # -- admin inventory ------------------------------------------------------

    def inventory(self, limit: int = 500) -> list[dict]:
        """Return cached entries (no payloads) for the Admin dashboard, newest first."""
        try:
            self._ensure_table()
            rows = self._sql(f"""
                SELECT table_fqn, tab, cached_at, cached_by,
                       (cached_at < current_timestamp() - INTERVAL {CAPABILITY_CACHE_TTL_SECONDS} SECONDS) AS stale
                FROM {CAP_CACHE_TABLE}
                WHERE value_json IS NOT NULL
                ORDER BY cached_at DESC
                LIMIT {int(limit)}
            """)
            out = []
            for r in rows:
                out.append({
                    "table_fqn": r.get("table_fqn"),
                    "tab": r.get("tab"),
                    "cached_at": str(r.get("cached_at")) if r.get("cached_at") else None,
                    "cached_by": r.get("cached_by"),
                    "stale": bool(r.get("stale") in (True, "true", "True", 1, "1")),
                })
            return out
        except Exception as e:
            logger.debug("CapabilityCache.inventory error: %s", e)
            return []
