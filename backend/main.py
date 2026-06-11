import json
import os
import re
import sys
import time
import asyncio
import hashlib
import logging
import resource
import threading
from collections import defaultdict, deque, OrderedDict

APP_VERSION = "1.3.0"

RATE_LIMIT_MAX_REQUESTS = int(os.environ.get("RATE_LIMIT_MAX_REQUESTS", "60"))
RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("RATE_LIMIT_WINDOW_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Metrics tracking for admin dashboard
# ---------------------------------------------------------------------------
_metrics_lock = threading.Lock()
_request_latencies: deque[tuple[float, float]] = deque(maxlen=1000)  # (timestamp, latency_ms)
_request_count = 0
_start_time = time.time()


def _record_latency(latency_ms: float):
    global _request_count
    with _metrics_lock:
        _request_latencies.append((time.time(), latency_ms))
        _request_count += 1
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from databricks.sdk import WorkspaceClient
from backend.lineage_service import (
    list_catalogs,
    list_schemas,
    list_all_tables,
    get_table_lineage,
    get_column_lineage,
    get_schema_column_lineage,
    get_columns,
    get_table_edges,
    resolve_entity_name,
    invalidate_cache,
    evict_cache_entry,
    get_cache_snapshot,
    _get_client,
)

class _JsonLogFormatter(logging.Formatter):
    """Structured JSON logs — one line per record so downstream log queries
    (Databricks app logs, Datadog, etc.) can filter by level/logger/message."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": round(record.created, 3),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


_log_handler = logging.StreamHandler()
_log_handler.setFormatter(_JsonLogFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_log_handler], force=True)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User identity + admin check — single API call using user's own token
#
# Per Databricks Apps docs, the proxy forwards the user's OAuth token via
# the `x-forwarded-access-token` header. We call current_user.me() with
# that token — the response includes the user's group memberships, so we
# can check admin status without any extra API calls or SPN permissions.
#
# Ref: https://docs.databricks.com/aws/en/dev-tools/databricks-apps/auth
# ---------------------------------------------------------------------------
ADMIN_GROUP_NAME = os.environ.get("ADMIN_GROUP_NAME", "admins")

# Cache: token_hash → (timestamp, email, is_admin)
# Keyed by token hash so cache is checked BEFORE any API call.
# LRU eviction at 1000 entries to bound memory.
_user_info_cache: OrderedDict[str, tuple[float, str | None, bool]] = OrderedDict()
_user_info_lock = threading.Lock()
USER_INFO_CACHE_TTL = 300  # 5 minutes (success)
USER_INFO_FAIL_TTL = 30  # short TTL for failed lookups so a bad token can't hammer the control plane
USER_INFO_CACHE_MAX = 1000


def _get_user_info(request: Request) -> tuple[str | None, bool]:
    """Return (email, is_admin) for the requesting user.

    Cache keyed by token hash — checked BEFORE API call to avoid
    thundering herd on the control plane. LRU-bounded at 1000 entries.
    Failed lookups are cached for 30s with email=None so repeated bad
    tokens don't generate fresh API calls every request.
    """
    user_token = request.headers.get("x-forwarded-access-token")
    if not user_token:
        # Local-dev override: when LOCAL_DEV_ADMIN_EMAIL is set, return that as an admin user.
        # The Apps proxy never sets x-forwarded-access-token in local uvicorn runs, so without
        # this override is_admin would always be false locally. Production deploys never set this.
        local_dev_email = os.environ.get("LOCAL_DEV_ADMIN_EMAIL")
        if local_dev_email:
            return local_dev_email, True
        logger.warning("No x-forwarded-access-token header — cannot identify user")
        return None, False

    token_hash = hashlib.sha256(user_token.encode()).hexdigest()[:16]
    now = time.time()

    with _user_info_lock:
        cached = _user_info_cache.get(token_hash)
        if cached:
            ts, email, is_admin = cached
            ttl = USER_INFO_CACHE_TTL if email is not None else USER_INFO_FAIL_TTL
            if now - ts < ttl:
                _user_info_cache.move_to_end(token_hash)
                return email, is_admin

    try:
        host = _get_client().config.host
        from databricks.sdk.core import Config as SdkConfig
        user_cfg = SdkConfig(host=host, token=user_token, auth_type="pat")
        user_client = WorkspaceClient(config=user_cfg)
        me = user_client.current_user.me()
        email = me.user_name

        is_admin = False
        if me.groups:
            is_admin = any(g.display == ADMIN_GROUP_NAME for g in me.groups)

        with _user_info_lock:
            _user_info_cache[token_hash] = (now, email, is_admin)
            _user_info_cache.move_to_end(token_hash)
            while len(_user_info_cache) > USER_INFO_CACHE_MAX:
                _user_info_cache.popitem(last=False)

        logger.info(f"User: {email}, admin: {is_admin}")
        return email, is_admin
    except Exception as e:
        logger.error(f"Failed to resolve user from x-forwarded-access-token: {e}")
        with _user_info_lock:
            _user_info_cache[token_hash] = (now, None, False)
            _user_info_cache.move_to_end(token_hash)
            while len(_user_info_cache) > USER_INFO_CACHE_MAX:
                _user_info_cache.popitem(last=False)
        return None, False


# ---------------------------------------------------------------------------
# Input validation — Databricks identifiers are alphanumeric + underscore only.
# Strict regex matches the README contract and forecloses SQL-quote escapes
# even though identifiers are interpolated through backticks/quotes downstream.
# ---------------------------------------------------------------------------
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]{1,255}$")
_JOB_ID_RE = re.compile(r"^[0-9]{1,32}$")
_PIPELINE_ID_RE = re.compile(r"^[a-fA-F0-9-]{8,64}$")
_NOTEBOOK_ID_RE = re.compile(r"^[A-Za-z0-9_./@ +-]{1,512}$")


def _validate_identifier(value: str, name: str) -> str:
    """Validate that a user-supplied identifier is safe for use in SQL."""
    value = value.strip()
    if not value:
        raise HTTPException(status_code=400, detail=f"{name} is required")
    if not _IDENTIFIER_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {name}: must be alphanumeric with underscores (got '{value[:50]}')",
        )
    return value


def _validate_entity_id(entity_type: str, entity_id: str) -> str:
    """Per-type validation for opaque entity ids interpolated into system table queries."""
    entity_id = entity_id.strip()
    if not entity_id:
        raise HTTPException(status_code=400, detail="entity_id is required")
    et = entity_type.upper()
    if et == "JOB":
        ok = bool(_JOB_ID_RE.match(entity_id))
    elif et == "PIPELINE":
        ok = bool(_PIPELINE_ID_RE.match(entity_id))
    elif et == "NOTEBOOK":
        ok = bool(_NOTEBOOK_ID_RE.match(entity_id))
    else:
        # Unknown entity types are passed through but constrained
        ok = bool(re.fullmatch(r"[A-Za-z0-9_./@ +-]{1,256}", entity_id))
    if not ok:
        raise HTTPException(status_code=400, detail=f"Invalid entity_id for {entity_type}")
    return entity_id


# ---------------------------------------------------------------------------
# Lifespan — clear stale caches on startup, preload table index, clean up on shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Increase thread pool for blocking SDK/SQL calls.
    # Default is min(32, os.cpu_count() + 4) = 8 on a 4-core app.
    # 64 threads allows ~20 concurrent SQL queries + user info lookups
    # while keeping single-process shared state (cache, coalescing, rate limits).
    import concurrent.futures
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=64))
    logger.info("Lineage Explorer starting up — thread pool set to 64 workers, clearing stale caches")
    invalidate_cache()
    # Pre-fetch per-entity cost cache in background so first lineage load shows cost.
    # The aggregation can take a few minutes against busy system.billing — it must
    # never run on the lineage hot path, only here and via the stale-cache tickler.
    from backend.lineage_service import _refresh_cost_cache, _get_client
    prefetch_task: asyncio.Task | None = None
    async def _prefetch_cost():
        try:
            client = _get_client()
            await asyncio.to_thread(_refresh_cost_cache, client)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(f"Failed to pre-fetch cost cache (will retry on first lineage load): {e}")
    prefetch_task = asyncio.create_task(_prefetch_cost())
    try:
        yield
    finally:
        logger.info("Lineage Explorer shutting down — cancelling background tasks, clearing caches")
        if prefetch_task and not prefetch_task.done():
            prefetch_task.cancel()
            try:
                await prefetch_task
            except (asyncio.CancelledError, Exception):
                pass
        invalidate_cache()


app = FastAPI(title="Lineage Explorer", version=APP_VERSION, lifespan=lifespan)


# ---------------------------------------------------------------------------
# Rate limiting middleware — protects DBSQL warehouse from abuse
# Keyed by user identity (x-forwarded-email or x-forwarded-access-token hash)
# instead of IP — all requests come from proxy in Databricks Apps.
# ---------------------------------------------------------------------------
MAX_TRACKED_USERS = 10_000


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user rate limiter with LRU eviction. Thread-safe via asyncio.Lock."""

    def __init__(self, app, max_requests: int = 60, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests: OrderedDict[str, list[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    def _get_user_key(self, request: Request) -> str:
        """Extract user identity for rate limiting. Falls back to IP."""
        token = request.headers.get("x-forwarded-access-token", "")
        if token:
            return hashlib.sha256(token.encode()).hexdigest()[:16]
        return request.client.host if request.client else "unknown"

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        user_key = self._get_user_key(request)
        now = time.time()

        async with self._lock:
            # LRU eviction
            if len(self.requests) > MAX_TRACKED_USERS:
                self.requests.popitem(last=False)

            # Prune old entries
            entries = self.requests.get(user_key, [])
            entries = [t for t in entries if now - t < self.window_seconds]

            if len(entries) >= self.max_requests:
                self.requests[user_key] = entries
                return JSONResponse(
                    status_code=429,
                    content={"detail": "Rate limit exceeded. Try again shortly."},
                )

            entries.append(now)
            self.requests[user_key] = entries
            self.requests.move_to_end(user_key)

        return await call_next(request)


app.add_middleware(RateLimitMiddleware, max_requests=RATE_LIMIT_MAX_REQUESTS, window_seconds=RATE_LIMIT_WINDOW_SECONDS)


class MetricsMiddleware(BaseHTTPMiddleware):
    """Records request latency for the admin dashboard."""

    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)
        start = time.time()
        response = await call_next(request)
        latency_ms = (time.time() - start) * 1000
        _record_latency(latency_ms)
        return response


app.add_middleware(MetricsMiddleware)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Defense-in-depth headers. The Databricks Apps proxy sets some of these,
    but app-level CSP is the only XSS protection for user-supplied content
    (table names, owners) rendered in the React shell."""

    CSP = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "  # Vite-built bundle uses inline runtime
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", self.CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        return response


app.add_middleware(SecurityHeadersMiddleware)


def _safe_error(e: Exception) -> str:
    """Return a sanitized error message safe for API responses (no internal paths/query details)."""
    msg = str(e)
    # Strip internal paths and query text
    if "SQL failed:" in msg:
        return "Query execution failed. Check warehouse availability and permissions."
    if "No SQL warehouse" in msg:
        return "No SQL warehouse available. Configure DATABRICKS_WAREHOUSE_ID."
    if len(msg) > 200:
        return msg[:200] + "..."
    return msg


# ---------------------------------------------------------------------------
# Health check — used by Databricks Apps orchestration
# ---------------------------------------------------------------------------
@app.get("/health")
async def health_check():
    return {"status": "ok", "version": APP_VERSION}


@app.get("/api/admin/status")
async def api_admin_status(request: Request):
    """Admin-only utilization dashboard — returns system metrics and cache status."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    from backend.lineage_service import CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES, CACHE_MAX_MEMORY_MB
    from datetime import datetime, timezone

    now = time.time()

    # Memory — Linux: /proc/self/status, fallback to resource module
    rss_mb = 0.0
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    rss_mb = round(int(line.split()[1]) / 1024, 1)  # KB → MB
                    break
    except Exception:
        try:
            rusage = resource.getrusage(resource.RUSAGE_SELF)
            rss_mb = round(rusage.ru_maxrss / 1024, 1)
        except Exception:
            pass

    # P50/P95/P99 latencies from last 1000 requests
    with _metrics_lock:
        latencies = sorted([l for _, l in _request_latencies])
    p50 = latencies[len(latencies) // 2] if latencies else 0
    p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
    p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0

    # Cache snapshot — lock held only to copy lightweight metadata (no serialization)
    entries_meta, total_cache_bytes, inflight_keys = get_cache_snapshot()

    cache_entries = []
    for key, created, last_accessed, size_bytes in entries_meta:
        age_sec = now - created
        ttl_remaining = max(0, CACHE_TTL_SECONDS - age_sec)
        cache_entries.append({
            "key": key,
            "cached_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
            "last_accessed": datetime.fromtimestamp(last_accessed, tz=timezone.utc).isoformat(),
            "last_accessed_ago": f"{int((now - last_accessed) / 60)}m ago" if now - last_accessed < 3600 else f"{int((now - last_accessed) / 3600)}h ago",
            "ttl_remaining_sec": int(ttl_remaining),
            "expired": ttl_remaining <= 0,
            "size_kb": round(size_bytes / 1024, 1),
        })
    total_entries = len(cache_entries)

    # Top 15 by size — prevents bloated API payloads and keeps dashboard snappy
    cache_entries.sort(key=lambda x: x["size_kb"], reverse=True)
    top_inventory = cache_entries[:15]

    # Thread pool info (defensive — private API)
    tp_workers = "unknown"
    try:
        loop = asyncio.get_running_loop()
        tp = getattr(loop, '_default_executor', None)
        if tp and hasattr(tp, '_max_workers'):
            tp_workers = tp._max_workers
    except Exception:
        pass

    max_bytes = CACHE_MAX_MEMORY_MB * 1024 * 1024
    return {
        "system": {
            "uptime_sec": int(now - _start_time),
            "uptime_human": f"{int((now - _start_time) / 3600)}h {int((now - _start_time) % 3600 / 60)}m",
            "python_version": sys.version.split()[0],
            "pid": os.getpid(),
        },
        "memory": {
            "rss_mb": rss_mb,
            "vms_mb": 0,
            "rss_percent": round(rss_mb / (6 * 1024) * 100, 1) if rss_mb > 0 else 0,
        },
        "latency": {
            "p50_ms": round(p50, 1),
            "p95_ms": round(p95, 1),
            "p99_ms": round(p99, 1),
            "sample_count": len(latencies),
        },
        "requests": {
            "total": _request_count,
            "rate_per_min": round(len([t for t, _ in _request_latencies if now - t < 60]), 1),
        },
        "thread_pool": {
            "max_workers": tp_workers,
            "inflight_cache_keys": inflight_keys,
        },
        "cache": {
            "entries": total_entries,
            "max_entries": CACHE_MAX_ENTRIES,
            "max_memory_mb": CACHE_MAX_MEMORY_MB,
            "ttl_seconds": CACHE_TTL_SECONDS,
            "utilization_percent": round(total_cache_bytes / max_bytes * 100, 1) if max_bytes > 0 else 0,
            "total_size_mb": round(total_cache_bytes / 1024 / 1024, 2),
            "inventory": top_inventory,
            "inventory_note": f"Top {len(top_inventory)} of {total_entries} by size",
        },
        "user_cache": {
            "entries": len(_user_info_cache),
            "max_entries": USER_INFO_CACHE_MAX,
        },
    }


# ---------------------------------------------------------------------------
# API endpoints — async wrappers around synchronous SDK calls
# ---------------------------------------------------------------------------

@app.get("/api/user-info")
async def api_user_info(request: Request):
    """Return current user identity and admin status."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    return {"email": email, "isAdmin": is_admin}


@app.get("/api/tables")
async def api_list_tables():
    """Return all tables across all catalogs (cached). Frontend filters client-side."""
    try:
        result = await asyncio.to_thread(list_all_tables)
        return {"tables": result}
    except Exception as e:
        logger.error(f"Error listing tables: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/catalogs")
async def api_list_catalogs():
    try:
        result = await asyncio.to_thread(list_catalogs)
        return {"catalogs": result}
    except Exception as e:
        logger.error(f"Error listing catalogs: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/schemas")
async def api_list_schemas(catalog: str = Query(...)):
    catalog = _validate_identifier(catalog, "catalog")
    try:
        result = await asyncio.to_thread(list_schemas, catalog)
        return {"schemas": result}
    except Exception as e:
        logger.error(f"Error listing schemas: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/lineage")
async def api_get_lineage(request: Request, catalog: str = Query(...), schema: str | None = Query(None), live: bool = Query(False)):
    catalog = _validate_identifier(catalog, "catalog")
    # schema is optional: omitting it builds catalog-wide lineage across all schemas
    if schema is not None:
        schema = _validate_identifier(schema, "schema")
    # Only admins can bypass cache with live mode
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        if live:
            scope = f"{catalog}.{schema}" if schema else f"{catalog} (catalog-wide)"
            logger.info(f"LIVE MODE: Serving lineage for {scope} direct from system tables")
        return await asyncio.to_thread(get_table_lineage, catalog, schema, live)
    except Exception as e:
        # Catalog-wide size cap is a client-actionable condition, not a server fault.
        if "exceeding the" in str(e) and "catalog-wide lineage" in str(e):
            raise HTTPException(status_code=413, detail=str(e))
        logger.error(f"Error getting lineage: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/columns")
async def api_get_columns(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    live: bool = Query(False),
):
    """Lazy column loader — fetch columns for a single table on demand."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        cols = await asyncio.to_thread(get_columns, catalog, schema, table, live)
        return {"columns": cols}
    except Exception as e:
        logger.error(f"Error getting columns: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/column-lineage")
async def api_get_column_lineage(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    column: str = Query(...),
    live: bool = Query(False),
):
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    column = _validate_identifier(column, "column")
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        return await asyncio.to_thread(get_column_lineage, catalog, schema, table, column, live)
    except Exception as e:
        logger.error(f"Error getting column lineage: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/schema-column-lineage")
async def api_get_schema_column_lineage(
    request: Request,
    catalog: str = Query(...),
    schema: str = Query(...),
    live: bool = Query(False),
):
    """All column lineage edges for a schema — used for transitive column tracing."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        return await asyncio.to_thread(get_schema_column_lineage, catalog, schema, live)
    except Exception as e:
        logger.error(f"Error getting schema column lineage: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/lineage/export")
async def api_export_lineage(
    request: Request,
    catalog: str = Query(...),
    schema: str | None = Query(None),
):
    """Stream a styled .xlsx of the lineage graph. Omit schema for catalog-wide.
    Column lineage is included for schema scope (it's per-schema)."""
    catalog = _validate_identifier(catalog, "catalog")
    if schema is not None:
        schema = _validate_identifier(schema, "schema")
    try:
        result = await asyncio.to_thread(get_table_lineage, catalog, schema, False)
        # Real recorded table→table pairs (with mediating entity) — accurate edges
        # for the Lineage sheet, instead of a cross-product reconstruction.
        table_edges = await asyncio.to_thread(get_table_edges, catalog, schema, False)
        column_edges = None
        if schema is not None:
            try:
                column_edges = await asyncio.to_thread(get_schema_column_lineage, catalog, schema, False)
            except Exception as ce:
                logger.warning(f"Column lineage unavailable for export {catalog}.{schema}: {ce}")

        # Resolve job/pipeline display names (for Lineage Map boxes + the "Via"
        # column). Cover entities from the graph AND from the real edge pairs.
        entity_names: dict[str, str] = {}
        entity_keys: set[tuple[str, str]] = set()
        for n in result.nodes:
            if getattr(n, "node_type", None) == "entity":
                if n.display_name:
                    entity_names[n.id] = n.display_name
                else:
                    entity_keys.add((n.entity_type, n.entity_id))
        for e in table_edges:
            if e.get("entity_type") and e.get("entity_id"):
                entity_keys.add((e["entity_type"], e["entity_id"]))
        for etype, eid in entity_keys:
            key = f"entity:{etype}:{eid}"
            if key in entity_names:
                continue
            nm = None
            try:
                nm = (await asyncio.to_thread(resolve_entity_name, etype, eid)).get("name")
            except Exception:
                nm = None
            entity_names[key] = nm or f"{etype} {eid[:8]}"

        from backend.excel_export import build_lineage_workbook
        data = await asyncio.to_thread(
            build_lineage_workbook, catalog, schema, result, column_edges, entity_names, table_edges
        )
    except ImportError:
        logger.error("openpyxl not installed — cannot build Excel export")
        raise HTTPException(status_code=503, detail="Excel export is temporarily unavailable on the server.")
    except HTTPException:
        raise
    except Exception as e:
        if "exceeding the" in str(e) and "catalog-wide lineage" in str(e):
            raise HTTPException(status_code=413, detail=str(e))
        logger.error(f"Error building lineage export: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))

    label = f"{catalog}.{schema}" if schema else catalog
    safe_label = re.sub(r"[^A-Za-z0-9._-]", "_", label)[:60]
    stamp = time.strftime("%Y-%m-%d")
    filename = f"lineage_{safe_label}_{stamp}.xlsx"
    return Response(
        content=data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/entity-name")
async def api_entity_name(entity_type: str = Query(...), entity_id: str = Query(...)):
    """Resolve an entity (job/pipeline/notebook) ID to a display name + metadata."""
    entity_type = _validate_identifier(entity_type, "entity_type")
    entity_id = _validate_entity_id(entity_type, entity_id)
    try:
        result = await asyncio.to_thread(resolve_entity_name, entity_type, entity_id)
        return result
    except Exception as e:
        logger.error(f"Error resolving entity name: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.post("/api/cache/invalidate")
async def api_invalidate_cache(request: Request):
    """Cache invalidation — protected: only callable from localhost or the app itself."""
    client_ip = request.client.host if request.client else ""
    if client_ip not in ("127.0.0.1", "::1", "localhost"):
        logger.warning(f"Cache invalidation attempt from external IP: {client_ip}")
        raise HTTPException(status_code=403, detail="Cache invalidation is restricted")
    invalidate_cache()
    return {"status": "ok", "message": "Cache cleared"}


@app.post("/api/admin/evict-cache")
async def api_admin_evict_cache(request: Request, key: str = Query(...)):
    """Admin-only: evict a specific cache entry by key."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    if evict_cache_entry(key):
        logger.info(f"Admin {email} evicted cache key: {key}")
        return {"status": "ok", "message": f"Evicted: {key}"}
    return {"status": "not_found", "message": f"Key not in cache: {key}"}


# ---------------------------------------------------------------------------
# Serve frontend static files — with path traversal protection
# ---------------------------------------------------------------------------
static_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(static_dir):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

    _index_path = os.path.join(static_dir, "index.html")

    def _index_response() -> FileResponse:
        # index.html must never be cached: it points at fingerprinted JS/CSS,
        # so a stale copy would keep loading the previous deploy's bundle.
        # (The hashed /assets files are safe to cache — their names change.)
        return FileResponse(_index_path, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        # Resolve the absolute path and ensure it stays within static_dir.
        # Use os.sep boundary so a sibling like `/static_dirextra` can't pass.
        file_path = os.path.realpath(os.path.join(static_dir, full_path))
        if not (file_path == static_dir or file_path.startswith(static_dir + os.sep)):
            return _index_response()
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return _index_response()
