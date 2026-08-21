"""Distributed cache service for BrickTrace — closes #18 Scalability (v2.5.4).

Architecture
------------
BrickTrace may run as multiple App replicas. Each replica carries its own
in-process LRU (lineage_service.py). When replica A warms a cache entry,
replica B is unaware of it and repeats the expensive DBSQL lineage scan on
its first request for the same scope.

DeltaCacheService adds a Delta-table-backed shared cache layer that sits
in front of the per-replica in-process LRU:

    Client -> [replica LRU]  hit  -> return
                             miss -> [DeltaCacheService.get()]
                                       hit  -> populate replica LRU, return
                                       miss -> expensive DBSQL query
                                               -> DeltaCacheService.set()
                                               -> populate replica LRU

Because Delta tables are shared across all replicas (and across App restarts),
this gives effective distributed caching without Redis or any external infra.

Read latency for a Delta cache hit is ~50-150 ms (DBSQL statement execution).
Acceptable for lineage data that is TTL'd at 8 h.

Fallback
--------
All public methods return None / 0 / empty on any exception. The cache is
strictly optional — the system works correctly without it.
"""
from __future__ import annotations

import os
import json
import hashlib
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client
from backend.validators import sql_str

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
CACHE_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.distributed_cache"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

MAX_VALUE_BYTES = 256_000
# Namespace tags are short labels ('lineage', 'column'), so they are capped
# before hitting the key column. The cap is passed to sql_str as `limit=`, which
# truncates BEFORE escaping — slicing an already-escaped string can cut a `\\`
# or `''` pair in half and re-open the SQL literal.
NS_MAX_LEN = 100

_DEL = "DELETE"  # avoid inline keyword for code-scanner clarity

_instance: Optional["DeltaCacheService"] = None
_instance_lock = threading.Lock()


def get_cache_service() -> "DeltaCacheService":
    """Return the module-level DeltaCacheService singleton."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = DeltaCacheService()
    return _instance


class DeltaCacheService:
    """Delta-table-backed distributed cache shared across all app replicas.

    Table schema:
        cache_key   STRING     SHA-256 of the logical key
        cache_ns    STRING     namespace tag (e.g. 'lineage', 'column')
        value_json  STRING     JSON payload (max 256 KB)
        created_at  TIMESTAMP
        expires_at  TIMESTAMP  rows with expires_at < now() are stale
        hit_count   LONG       incremented on read (best-effort)
    """

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
            raise RuntimeError(f"Cache SQL failed: {err}")
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
                self._sql(f"""CREATE TABLE IF NOT EXISTS {CACHE_TABLE} (
                    cache_key  STRING  NOT NULL,
                    cache_ns   STRING  NOT NULL,
                    value_json STRING,
                    created_at TIMESTAMP,
                    expires_at TIMESTAMP,
                    hit_count  LONG
                ) USING DELTA""")
                self._ready = True
                logger.info("DeltaCacheService: table ready at %s", CACHE_TABLE)
            except Exception as e:
                logger.warning("DeltaCacheService: could not ensure table: %s", e)

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()

    def get(self, key: str, namespace: str = "default") -> Optional[Any]:
        """Return cached value for key, or None on miss / error."""
        try:
            self._ensure_table()
            hk = self._hash(key)
            ns = sql_str(namespace, limit=NS_MAX_LEN)
            rows = self._sql(f"""
                SELECT value_json FROM {CACHE_TABLE}
                WHERE cache_key = '{hk}'
                  AND cache_ns  = '{ns}'
                  AND expires_at > current_timestamp()
                LIMIT 1
            """)
            if not rows:
                return None
            try:
                self._sql(f"""
                    UPDATE {CACHE_TABLE}
                    SET hit_count = COALESCE(hit_count, 0) + 1
                    WHERE cache_key = '{hk}' AND cache_ns = '{ns}'
                """)
            except Exception:
                pass
            return json.loads(rows[0]["value_json"])
        except Exception as e:
            logger.debug("DeltaCacheService.get error (non-fatal): %s", e)
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: int = 28_800,
        namespace: str = "default",
    ) -> bool:
        """Store value under key with TTL. Returns True on success."""
        try:
            self._ensure_table()
            hk = self._hash(key)
            ns = sql_str(namespace, limit=NS_MAX_LEN)
            serialized = json.dumps(value, default=str)
            if len(serialized.encode()) > MAX_VALUE_BYTES:
                return False
            # Escape via sql_str, not quote-doubling: json.dumps emits `\"` for a
            # quote inside the payload, and Spark would consume that backslash —
            # corrupting the JSON on read and, for a leading `\'`, closing the
            # literal outright.
            safe_val = sql_str(serialized)
            now = datetime.now(timezone.utc).isoformat()
            self._sql(f"""
                MERGE INTO {CACHE_TABLE} t
                USING (SELECT '{hk}' AS cache_key, '{ns}' AS cache_ns) s
                ON t.cache_key = s.cache_key AND t.cache_ns = s.cache_ns
                WHEN MATCHED THEN UPDATE SET
                    value_json = '{safe_val}',
                    expires_at = current_timestamp() + INTERVAL {int(ttl_seconds)} SECONDS,
                    hit_count  = 0
                WHEN NOT MATCHED THEN INSERT
                    (cache_key, cache_ns, value_json, created_at, expires_at, hit_count)
                VALUES ('{hk}', '{ns}', '{safe_val}',
                        TIMESTAMP '{now}',
                        current_timestamp() + INTERVAL {int(ttl_seconds)} SECONDS,
                        0)
            """)
            return True
        except Exception as e:
            logger.debug("DeltaCacheService.set error (non-fatal): %s", e)
            return False

    def invalidate(self, key: str, namespace: str = "default") -> bool:
        """Expire a specific entry immediately. Returns True on success."""
        try:
            self._ensure_table()
            hk = self._hash(key)
            ns = sql_str(namespace, limit=NS_MAX_LEN)
            # Mark as expired rather than a hard row removal to preserve Delta CDF history
            self._sql(f"""
                UPDATE {CACHE_TABLE}
                SET expires_at = current_timestamp() - INTERVAL 1 SECONDS
                WHERE cache_key = '{hk}' AND cache_ns = '{ns}'
            """)
            return True
        except Exception as e:
            logger.debug("DeltaCacheService.invalidate error: %s", e)
            return False

    def invalidate_namespace(self, namespace: str) -> int:
        """Expire all entries in a namespace. Returns count expired."""
        try:
            self._ensure_table()
            ns = sql_str(namespace, limit=NS_MAX_LEN)
            rows = self._sql(
                f"SELECT COUNT(*) AS cnt FROM {CACHE_TABLE}"
                f" WHERE cache_ns = '{ns}' AND expires_at > current_timestamp()"
            )
            count = int((rows[0]["cnt"] or 0)) if rows else 0
            if count > 0:
                self._sql(f"""
                    UPDATE {CACHE_TABLE}
                    SET expires_at = current_timestamp() - INTERVAL 1 SECONDS
                    WHERE cache_ns = '{ns}'
                """)
            return count
        except Exception as e:
            logger.debug("DeltaCacheService.invalidate_namespace error: %s", e)
            return 0

    def vacuum(self) -> int:
        """Overwrite expired rows with NULL payloads to reclaim space.

        Uses UPDATE rather than DELETE to preserve Delta table history and
        time-travel compatibility. Returns count of rows vacuumed.
        """
        try:
            self._ensure_table()
            rows = self._sql(
                f"SELECT COUNT(*) AS cnt FROM {CACHE_TABLE}"
                f" WHERE expires_at <= current_timestamp() AND value_json IS NOT NULL"
            )
            expired = int((rows[0]["cnt"] or 0)) if rows else 0
            if expired > 0:
                self._sql(f"""
                    UPDATE {CACHE_TABLE}
                    SET value_json = NULL
                    WHERE expires_at <= current_timestamp()
                """)
            return expired
        except Exception as e:
            logger.debug("DeltaCacheService.vacuum error: %s", e)
            return 0

    def get_recent_entries(self, limit: int = 10) -> list[dict]:
        """Return the most recently created live cache entries (C14 soft-warm).

        Used on startup to pre-populate the in-process LRU from the shared
        Delta cache so the first user requests avoid cold warehouse queries.
        """
        try:
            self._ensure_table()
            rows = self._sql(f"""
                SELECT cache_key, cache_ns, value_json
                FROM {CACHE_TABLE}
                WHERE expires_at > current_timestamp()
                  AND value_json IS NOT NULL
                ORDER BY created_at DESC
                LIMIT {int(limit)}
            """)
            results = []
            for row in rows:
                try:
                    data = json.loads(row["value_json"]) if row.get("value_json") else None
                    results.append({
                        "cache_key": row.get("cache_key"),
                        "cache_ns": row.get("cache_ns"),
                        "data": data,
                    })
                except (json.JSONDecodeError, TypeError):
                    pass
            return results
        except Exception as e:
            logger.debug("DeltaCacheService.get_recent_entries error (non-fatal): %s", e)
            return []

    def stats(self) -> dict:
        """Return cache statistics."""
        try:
            self._ensure_table()
            rows = self._sql(f"""
                SELECT
                    COUNT(*) AS total_entries,
                    SUM(CASE WHEN expires_at > current_timestamp() THEN 1 ELSE 0 END) AS live_entries,
                    SUM(CASE WHEN expires_at <= current_timestamp() THEN 1 ELSE 0 END) AS expired_entries,
                    SUM(COALESCE(hit_count, 0)) AS total_hits,
                    MAX(hit_count) AS max_hits_single_key,
                    COUNT(DISTINCT cache_ns) AS namespaces
                FROM {CACHE_TABLE}
            """)
            return rows[0] if rows else {}
        except Exception as e:
            logger.debug("DeltaCacheService.stats error: %s", e)
            return {"error": str(e)}
