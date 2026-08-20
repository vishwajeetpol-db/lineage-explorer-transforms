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
from collections import deque, OrderedDict
from dataclasses import asdict

# A5 FIX: Single source of truth for version. Sync with package.json, README, CHANGELOG.
APP_VERSION = "2.6.0"

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
    _CATALOG_ALLOWLIST,
    list_catalogs,
    list_schemas,
    list_all_tables,
    get_table_lineage,
    get_lineage_trace,
    get_column_lineage,
    get_schema_column_lineage,
    get_columns,
    get_table_edges,
    get_sharing_overlay,
    get_sharing_overview,
    get_federated_source_overlay,
    resolve_entity_name,
    invalidate_cache,
    evict_cache_entry,
    get_cache_snapshot,
    _get_client,
)
from backend.transform_service import (
    get_transform_freshness,
    backtrack_transform_lineage,
    get_transform_categories,
    invalidate_transform_cache,
    clear_transform_lineage,
    diagnose_missing_lineage,
)
from backend.build_service import (
    submit_build_job,
    get_build_status,
    is_build_configured,
    get_pipeline_notebook_path,
    BUILD_STEPS,
)
from backend.models import BuildJobRequest
from backend.feature_flags import list_flags, set_flag_state, check_access_requirements
from backend.plan_capture_service import get_plan_capture_status, get_captured_expression
from backend.federated_sync import get_federated_sync_status, list_federated_peers, register_federated_peer
from backend.edge_case_guards import (
    build_graph_warnings,
    apply_graph_truncation,
    MAX_GRAPH_NODES,
)

# --- Routers for capabilities 17-36 ---
from backend.routes.governance import router as governance_router
from backend.routes.impact import router as impact_router
from backend.routes.observability import router as observability_router
from backend.routes.access import router as access_router
from backend.routes.ml import router as ml_router
from backend.routes.discovery import router as discovery_router
from backend.routes.dq import router as dq_router
from backend.routes.lineage import router as lineage_ext_router, analyze_router
from backend.routes.pipeline_installer import router as pipeline_installer_router
from backend.routes.diagnostics import router as diagnostics_router
from backend.routes.root_cause import router as root_cause_router
from backend.routes.glossary import router as glossary_router
from backend.routes.capability_closures import router as capability_closures_router
from backend.routes.notifications import router as notifications_router
from backend.routes.openlineage import router as openlineage_router
from backend.routes.external_sources import router as external_sources_router
from backend.routes.graph_snapshots import router as graph_snapshots_router
from backend.routes.scalability import router as scalability_router

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
        # Local-dev override: when LOCAL_DEV_ADMIN_EMAIL is set AND not running as a
        # deployed Databricks App, return that as an admin user. The Apps proxy never
        # sets x-forwarded-access-token in local uvicorn runs, so without this override
        # is_admin would always be false locally.
        # A14 FIX: Block this backdoor when DATABRICKS_APP_NAME is set (deployed App).
        local_dev_email = os.environ.get("LOCAL_DEV_ADMIN_EMAIL")
        is_deployed_app = bool(os.environ.get("DATABRICKS_APP_NAME"))
        if local_dev_email and not is_deployed_app:
            logger.info(f"LOCAL_DEV_ADMIN_EMAIL active (local dev): {local_dev_email}")
            return local_dev_email, True
        if local_dev_email and is_deployed_app:
            logger.critical("SECURITY: LOCAL_DEV_ADMIN_EMAIL is set on a deployed App — ignoring it")
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
# C15 FIX: Accept hyphens in identifiers — Unity Catalog allows them (e.g. "my-catalog").
# Previously this regex rejected hyphens, inconsistent with backend/validators.py.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")
_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}\.[A-Za-z0-9_-]{1,255}\.[A-Za-z0-9_-]{1,255}$")
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
    logger.info("BrickTrace starting up — thread pool set to 64 workers, clearing stale caches")
    # Activate performance patches (parallelism wrappers for catalog enumeration,
    # BFS trace walks, cost cache refresh, and column fetch). Idempotent.
    from backend.startup import activate as _activate_perf
    _activate_perf()
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
        logger.info("BrickTrace shutting down — cancelling background tasks, clearing caches")
        if prefetch_task and not prefetch_task.done():
            prefetch_task.cancel()
            try:
                await prefetch_task
            except (asyncio.CancelledError, Exception):
                pass
        invalidate_cache()


app = FastAPI(title="BrickTrace", version=APP_VERSION, lifespan=lifespan)


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
        """Extract user identity for rate limiting.

        A11 FIX: Prefer x-forwarded-email (set by Apps proxy for identified users)
        before falling back to token hash. Without this, all users behind the proxy
        share a single rate-limit bucket keyed by the proxy's IP.
        """
        # Best: email header (unique per user, set by Apps proxy)
        email = request.headers.get("x-forwarded-email", "")
        if email:
            return hashlib.sha256(email.encode()).hexdigest()[:16]
        # Good: token hash (unique per session)
        token = request.headers.get("x-forwarded-access-token", "")
        if token:
            return hashlib.sha256(token.encode()).hexdigest()[:16]
        # Fallback: IP (shared bucket — not ideal but last resort)
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

# ---------------------------------------------------------------------------
# Register all API routers — capabilities 05-22+
# ---------------------------------------------------------------------------
app.include_router(governance_router)
app.include_router(impact_router)
app.include_router(observability_router)
app.include_router(access_router)
app.include_router(ml_router)
app.include_router(discovery_router)
app.include_router(dq_router)
app.include_router(lineage_ext_router)
app.include_router(analyze_router)
app.include_router(pipeline_installer_router)
app.include_router(diagnostics_router)
# New capability routers (closing gaps + completing partials)
app.include_router(root_cause_router)
app.include_router(glossary_router)
app.include_router(capability_closures_router)
app.include_router(notifications_router)
app.include_router(openlineage_router)
app.include_router(external_sources_router)
app.include_router(graph_snapshots_router)
app.include_router(scalability_router)


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
    """Health endpoint enriched with edge-case guard diagnostics (C1, C7)."""
    try:
        from backend.edge_case_guards import get_health_status_dict
        from backend.lineage_service import _execute_sql
        guards = await asyncio.to_thread(get_health_status_dict, _execute_sql)
    except Exception:
        guards = {"error": "guards unavailable"}
    return {
        "status": "ok",
        "version": APP_VERSION,
        "system_health": guards,
    }


@app.get("/api/capture/prerequisites")
async def api_capture_prerequisites():
    """C11: Check plan-capture prerequisites (flags, schema, table)."""
    try:
        from backend.edge_case_guards import check_capture_prerequisites
        from backend.lineage_service import _execute_sql
        result = await asyncio.to_thread(check_capture_prerequisites, _execute_sql)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"capture_ready": False, "error": str(e)}, status_code=500)


@app.get("/api/scd-detection")
async def api_scd_detection(table: str = Query(...)):
    """C16: Detect SCD/CDC patterns in a table."""
    table = table.strip()
    if not _FULL_NAME_RE.match(table):
        raise HTTPException(status_code=400, detail="table must be catalog.schema.table")
    try:
        from backend.edge_case_guards import detect_scd_cdc_patterns
        from backend.lineage_service import _execute_sql
        result = await asyncio.to_thread(detect_scd_cdc_patterns, table, _execute_sql)
        return JSONResponse(result)
    except Exception as e:
        return JSONResponse({"table_fqn": table, "error": str(e)}, status_code=500)


@app.get("/api/diagnostics")
async def api_diagnostics():
    """Deploy self-check: reports which prerequisites (warehouse, system.access,
    system.billing, information_schema, catalog BROWSE) are actually reachable by
    the app's service principal. Intentionally unauthenticated — it's used to
    troubleshoot a fresh deploy before admin OAuth scopes are wired up, and it
    exposes only reachability status, never row data."""
    from backend.lineage_service import run_diagnostics
    result = await asyncio.to_thread(run_diagnostics)
    return JSONResponse(result, status_code=200 if result["ok"] else 503)


@app.get("/api/admin/status")
async def api_admin_status(request: Request):
    """Admin-only utilization dashboard — returns system metrics and cache status."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")

    from backend.lineage_service import CACHE_TTL_SECONDS, CACHE_MAX_ENTRIES, CACHE_MAX_MEMORY_MB
    from datetime import datetime, timezone

    now = time.time()

    # Memory — Linux: read VmRSS from /proc/self/status; fall back to resource module.
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
            "catalog_allowlist_active": _CATALOG_ALLOWLIST is not None,
            "catalog_allowlist": sorted(_CATALOG_ALLOWLIST) if _CATALOG_ALLOWLIST else None,
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
        result = await asyncio.to_thread(get_table_lineage, catalog, schema, live)
        # C4: Truncate large graphs BEFORE serializing — bounds memory and response size.
        # apply_graph_truncation works on dicts; convert, truncate, then trim the model.
        nodes_raw = [n.model_dump() if hasattr(n, 'model_dump') else n for n in result.nodes]
        edges_raw = [e.model_dump() if hasattr(e, 'model_dump') else e for e in result.edges]
        nodes_raw, edges_raw, was_truncated, _ = apply_graph_truncation(nodes_raw, edges_raw)
        if was_truncated:
            result.nodes = result.nodes[:len(nodes_raw)]
            retained_ids = {n.get("id") or n.get("name") for n in nodes_raw}
            result.edges = [e for e in result.edges if e.source in retained_ids and e.target in retained_ids]
            result.table_edges = [e for e in result.table_edges if e.source in retained_ids and e.target in retained_ids]
            result.truncated = True
        # C2/C3/C5: Enrich response with graph warnings (C4 truncation already applied above)
        requested_cats = [catalog]
        accessible_cats = list(_CATALOG_ALLOWLIST) if _CATALOG_ALLOWLIST else [catalog]
        warnings = build_graph_warnings(
            nodes=nodes_raw,
            edges=edges_raw,
            accessible_catalogs=accessible_cats,
            requested_catalogs=requested_cats,
        )
        result.graph_warnings = asdict(warnings)
        return result
    except Exception as e:
        # Catalog-wide size cap is a client-actionable condition, not a server fault.
        if "exceeding the" in str(e) and "catalog-wide lineage" in str(e):
            raise HTTPException(status_code=413, detail=str(e))
        logger.error(f"Error getting lineage: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/lineage/trace")
async def api_lineage_trace(request: Request, table: str = Query(...), live: bool = Query(False)):
    """End-to-end cross-catalog lineage trace from a single seed table.
    Walks system.access.table_lineage in both directions across all catalogs."""
    table = table.strip()
    if not _FULL_NAME_RE.match(table):
        raise HTTPException(status_code=400, detail="table must be a fully-qualified catalog.schema.table name")
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        result = await asyncio.to_thread(get_lineage_trace, table, live)
        # C4: Truncate large graphs BEFORE serializing
        nodes_raw = [n.model_dump() if hasattr(n, 'model_dump') else n for n in result.nodes]
        edges_raw = [e.model_dump() if hasattr(e, 'model_dump') else e for e in result.edges]
        nodes_raw, edges_raw, was_truncated, _ = apply_graph_truncation(nodes_raw, edges_raw)
        if was_truncated:
            result.nodes = result.nodes[:len(nodes_raw)]
            retained_ids = {n.get("id") or n.get("name") for n in nodes_raw}
            result.edges = [e for e in result.edges if e.source in retained_ids and e.target in retained_ids]
            result.table_edges = [e for e in result.table_edges if e.source in retained_ids and e.target in retained_ids]
            result.truncated = True
        # C2/C3/C5: Trace crosses catalogs — extract all referenced catalogs from nodes
        requested_cats = list({n.get("catalog", "") for n in nodes_raw if n.get("catalog")})
        accessible_cats = list(_CATALOG_ALLOWLIST) if _CATALOG_ALLOWLIST else requested_cats
        warnings = build_graph_warnings(
            nodes=nodes_raw,
            edges=edges_raw,
            accessible_catalogs=accessible_cats,
            requested_catalogs=requested_cats,
        )
        result.graph_warnings = asdict(warnings)
        return result
    except Exception as e:
        logger.error(f"Error tracing lineage for {table}: {e}")
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


@app.get("/api/sharing/overlay")
async def api_sharing_overlay(
    request: Request,
    catalog: str = Query(...),
    schema: str | None = Query(None),
    audience: str = Query("both"),
    live: bool = Query(False),
):
    """Delta Sharing overlay for a lineage scope — outbound (shared tables) and/or
    inbound (foreign catalogs). The frontend merges this onto the current graph."""
    catalog = _validate_identifier(catalog, "catalog")
    if schema is not None:
        schema = _validate_identifier(schema, "schema")
    if audience not in ("provider", "recipient", "both"):
        audience = "both"
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        return await asyncio.to_thread(get_sharing_overlay, catalog, schema, audience, live)
    except Exception as e:
        logger.error(f"Error getting sharing overlay: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/sharing/overview")
async def api_sharing_overview(request: Request, live: bool = Query(False)):
    """Metastore-wide Delta Sharing inventory for the landing 'Sharing overview' card."""
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        return await asyncio.to_thread(get_sharing_overview, live)
    except Exception as e:
        logger.error(f"Error getting sharing overview: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/lineage/federated-overlay")
async def api_federated_overlay(
    request: Request,
    live: bool = Query(False),
):
    """Federated Source overlay for Lakehouse Federation — enriches FOREIGN table
    nodes with connection metadata (type, remote schema/object). Mirrors the
    Delta Sharing overlay pattern; lazy-loaded via a toolbar toggle."""
    if live:
        _, is_admin = await asyncio.to_thread(_get_user_info, request)
        if not is_admin:
            live = False
    try:
        return await asyncio.to_thread(get_federated_source_overlay, live)
    except Exception as e:
        logger.error(f"Error getting federated source overlay: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


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
    """Cache invalidation — ADMIN ONLY. (Was IP-gated to localhost, but behind the
    Databricks Apps proxy request.client.host is the proxy — which can resolve to
    localhost — so the IP check failed open and let any authenticated user flush
    the cache. Gate by admin identity instead.)"""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        logger.warning(f"Non-admin cache invalidation attempt by {email}")
        raise HTTPException(status_code=403, detail="Admin access required")
    invalidate_cache()
    logger.info(f"Admin {email} invalidated the full cache")
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
# Per-table capability cache (Impact / Root Cause / Governance / Access)
# ---------------------------------------------------------------------------


@app.get("/api/admin/capability-cache")
async def api_admin_capability_cache_inventory(request: Request):
    """Admin-only: list cached (table, tab) capability entries for the dashboard."""
    _email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    from backend.server.capability_cache import get_capability_cache
    entries = await asyncio.to_thread(get_capability_cache().inventory)
    return {"entries": entries, "count": len(entries)}


@app.post("/api/admin/capability-cache/evict")
async def api_admin_capability_cache_evict(
    request: Request,
    scope: str = Query(..., description="one of: entry | table | all"),
    table_fqn: str | None = Query(None),
    tab: str | None = Query(None),
):
    """Admin-only: evict capability-cache entries.

    scope=entry → one (table_fqn, tab); scope=table → all tabs for table_fqn;
    scope=all → the entire capability cache.
    """
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    from backend.server.capability_cache import get_capability_cache
    cache = get_capability_cache()
    if scope == "entry":
        if not table_fqn or not tab:
            raise HTTPException(status_code=400, detail="table_fqn and tab required for scope=entry")
        ok = await asyncio.to_thread(cache.evict, table_fqn, tab)
        logger.info(f"Admin {email} evicted capability cache {table_fqn}/{tab}")
        return {"status": "ok" if ok else "error", "scope": scope, "table_fqn": table_fqn, "tab": tab}
    if scope == "table":
        if not table_fqn:
            raise HTTPException(status_code=400, detail="table_fqn required for scope=table")
        n = await asyncio.to_thread(cache.evict_table, table_fqn)
        logger.info(f"Admin {email} evicted {n} capability-cache entries for {table_fqn}")
        return {"status": "ok", "scope": scope, "table_fqn": table_fqn, "evicted": n}
    if scope == "all":
        n = await asyncio.to_thread(cache.evict_all)
        logger.info(f"Admin {email} evicted the entire capability cache ({n} entries)")
        return {"status": "ok", "scope": scope, "evicted": n}
    raise HTTPException(status_code=400, detail="scope must be one of: entry | table | all")


# ---------------------------------------------------------------------------
# Transformation Lineage endpoints — the "microscopic" drill-down
# ---------------------------------------------------------------------------


@app.get("/api/transform/freshness")
async def api_transform_freshness(
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Check if transformation lineage exists for a table and whether it's stale."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    try:
        result = await asyncio.to_thread(get_transform_freshness, catalog, schema, table)
        return result
    except Exception as e:
        logger.error(f"Error checking transform freshness: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/transform/diagnose")
async def api_transform_diagnose(
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
):
    """Explain why a table has no transformation lineage (e.g. producer outside the
    discovery window, unresolvable producer, or a source table) — shown instead of
    a generic "not generated yet" when a build materialized nothing."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    try:
        return await asyncio.to_thread(diagnose_missing_lineage, catalog, schema, table)
    except Exception as e:
        logger.error(f"Error diagnosing transform lineage for {catalog}.{schema}.{table}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.post("/api/transform/invalidate")
async def api_transform_invalidate(
    request: Request,
    scope: str = Query("cache"),
    table_fqn: str = Query(None),
):
    """Invalidate transformation lineage. ADMIN ONLY.

    scope=cache  → flush in-memory transform caches (no data loss).
    scope=table  → also delete one table's stored edges (needs table_fqn).
    scope=all    → wipe ALL stored transformation lineage (start fresh).
    """
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        logger.warning(f"Non-admin transform invalidate attempt by {email}")
        raise HTTPException(status_code=403, detail="Admin access required")
    if scope not in ("cache", "table", "all"):
        raise HTTPException(status_code=400, detail="scope must be cache|table|all")
    if scope == "table":
        if not table_fqn or not _FULL_NAME_RE.match(table_fqn):
            raise HTTPException(status_code=400, detail="table_fqn (catalog.schema.table) required for scope=table")
    try:
        result = await asyncio.to_thread(clear_transform_lineage, scope, table_fqn)
        logger.info(f"Admin {email} invalidated transform lineage scope={scope} table={table_fqn}")
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"Transform invalidate failed: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.post("/api/transform/build")
async def api_transform_build(request: Request, body: BuildJobRequest):
    """Submit a serverless job to build transformation lineage for a table.
    Requires the PIPELINE_NOTEBOOK_PATH to be configured.

    A2 FIX: Admin-gated — builds submit serverless Jobs as the App SP,
    consuming warehouse compute. Non-admins should not trigger builds.
    """
    # A2 FIX: Require admin for expensive build operations
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required to submit build jobs")
    if not is_build_configured():
        raise HTTPException(
            status_code=503,
            detail="Build pipeline not configured. Set PIPELINE_NOTEBOOK_PATH in databricks.yml.",
        )

    table_fqn = body.table_fqn.strip()
    # Validate FQN format (catalog.schema.table)
    if not _FULL_NAME_RE.match(table_fqn):
        raise HTTPException(
            status_code=400,
            detail="table_fqn must be fully qualified: catalog.schema.table",
        )
    parts = table_fqn.split(".")
    catalog, schema, table = parts[0], parts[1], parts[2]

    # Check freshness first (unless force_rebuild)
    if not body.force_rebuild:
        freshness = await asyncio.to_thread(
            get_transform_freshness, catalog, schema, table
        )
        if freshness.exists and not freshness.is_stale:
            return {
                "status": "fresh",
                "message": f"Lineage is fresh ({freshness.age_str}). Use force_rebuild=true to rebuild.",
                "freshness": freshness,
            }

    try:
        # A force_rebuild also forces a full re-parse (bypass change detection),
        # so "Regenerate" / clear-and-rebuild actually re-runs the parser.
        run_id = await asyncio.to_thread(
            submit_build_job, table_fqn, catalog, schema, bool(body.force_rebuild)
        )
        return {
            "status": "submitted",
            "run_id": run_id,
            "table_fqn": table_fqn,
            "steps": BUILD_STEPS,
        }
    except Exception as e:
        logger.error(f"Error submitting build job for {table_fqn}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/transform/status/{run_id}")
async def api_transform_status(run_id: str):
    """Poll the progress of a running transformation lineage build job."""
    if not run_id.isdigit():
        raise HTTPException(status_code=400, detail="run_id must be numeric")
    try:
        result = await asyncio.to_thread(get_build_status, run_id)
        # On successful completion, invalidate transform cache so next
        # trace query picks up the fresh edges.
        if result.is_complete and result.is_success:
            invalidate_transform_cache()
        return result
    except Exception as e:
        logger.error(f"Error getting build status for run {run_id}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/transform/trace")
async def api_transform_trace(
    catalog: str = Query(...),
    schema: str = Query(...),
    table: str = Query(...),
    column: str = Query(...),
    max_depth: int = Query(None),
):
    """Backtrack upstream transformation lineage for a specific column.
    Returns a layered graph of columns, expressions, and categories."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    column = _validate_identifier(column, "column")
    try:
        result = await asyncio.to_thread(
            backtrack_transform_lineage, catalog, schema, table, column, max_depth
        )
        return result
    except Exception as e:
        logger.error(f"Error tracing transform lineage for {catalog}.{schema}.{table}.{column}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/transform/categories")
async def api_transform_categories():
    """Return transformation category → color mapping for the frontend legend."""
    return {"categories": get_transform_categories()}


@app.get("/api/transform/build-configured")
async def api_transform_build_configured():
    """Check if the build pipeline is configured (PIPELINE_NOTEBOOK_PATH set)."""
    return {"configured": is_build_configured()}


# ---------------------------------------------------------------------------
# Control Panel — feature flags, workspace impact/access metadata, and the
# gated read paths for Runtime Plan Capture and Federated Sync. Every flag
# defaults to OFF; enabling one only changes behavior in the modules that
# explicitly check get_flag_state() (plan_capture_service, federated_sync).
# See backend/feature_flags.py, backend/plan_capture_service.py,
# backend/federated_sync.py, and docs/architecture.md.
# ---------------------------------------------------------------------------


@app.get("/api/control-panel/flags")
async def api_control_panel_flags():
    """List all Control Panel capabilities with current enabled state, cost/risk/
    side-effect metadata, and static access requirements."""
    try:
        flags = await asyncio.to_thread(list_flags)
        return {"flags": flags}
    except Exception as e:
        logger.error(f"Error listing feature flags: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/control-panel/access-check/{flag_id}")
async def api_control_panel_access_check(flag_id: str):
    """Best-effort live check of the access requirements for one flag."""
    try:
        requirements = await asyncio.to_thread(check_access_requirements, flag_id)
        return {"flag_id": flag_id, "requirements": requirements}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error checking access for flag {flag_id}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.post("/api/control-panel/flags/{flag_id}")
async def api_control_panel_set_flag(request: Request, flag_id: str, body: dict):
    """Enable/disable a Control Panel capability. ADMIN ONLY — a toggle here
    changes app-wide behavior (e.g. whether plan-capture reads are consulted),
    not just this user's session."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        logger.warning(f"Non-admin feature-flag change attempt by {email} on {flag_id}")
        raise HTTPException(status_code=403, detail="Admin access required")
    enabled = bool(body.get("enabled", False))
    try:
        result = await asyncio.to_thread(set_flag_state, flag_id, enabled, email or "unknown")
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error setting feature flag {flag_id}: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/control-panel/plan-capture/status")
async def api_plan_capture_status():
    """Runtime Plan Capture status card — captured plan/CDC-spec counts. Returns
    all-zero/disabled when the flag is off or the table isn't reachable yet."""
    try:
        return await asyncio.to_thread(get_plan_capture_status)
    except Exception as e:
        logger.error(f"Error getting plan capture status: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/transform/captured-expression")
async def api_transform_captured_expression(
    catalog: str = Query(...), schema: str = Query(...), table: str = Query(...), column: str = Query(...),
):
    """Additive enrichment: the Runtime-Captured-Plan expression for one column,
    if Runtime Plan Capture is enabled and a plan has been captured for this
    target. Returns null (not an error) when unavailable — the frontend falls
    back to the static-parse expression it already has from /api/transform/trace."""
    catalog = _validate_identifier(catalog, "catalog")
    schema = _validate_identifier(schema, "schema")
    table = _validate_identifier(table, "table")
    column = _validate_identifier(column, "column")
    try:
        result = await asyncio.to_thread(get_captured_expression, catalog, schema, table, column)
        return {"captured": result}
    except Exception as e:
        logger.error(f"Error getting captured expression: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/control-panel/federated/status")
async def api_federated_sync_status():
    """Federated Sync status card — registered peers vs. known Delta Shares."""
    try:
        return await asyncio.to_thread(get_federated_sync_status)
    except Exception as e:
        logger.error(f"Error getting federated sync status: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.get("/api/control-panel/federated/peers")
async def api_federated_list_peers():
    try:
        return {"peers": await asyncio.to_thread(list_federated_peers)}
    except Exception as e:
        logger.error(f"Error listing federated peers: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


@app.post("/api/control-panel/federated/peers")
async def api_federated_register_peer(request: Request, body: dict):
    """Register a known peer workspace/metastore for the Federated Sync overlay.
    ADMIN ONLY — this changes what every user sees as a 'known' shared boundary."""
    email, is_admin = await asyncio.to_thread(_get_user_info, request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    peer_alias = str(body.get("peer_alias", "")).strip()
    share_name = str(body.get("share_name", "")).strip()
    direction = str(body.get("direction", "both")).strip()
    notes = str(body.get("notes", "")).strip()
    if not peer_alias or not share_name:
        raise HTTPException(status_code=400, detail="peer_alias and share_name are required")
    try:
        result = await asyncio.to_thread(
            register_federated_peer, peer_alias, share_name, direction, email or "unknown", notes
        )
        return {"status": "ok", **result}
    except Exception as e:
        logger.error(f"Error registering federated peer: {e}")
        raise HTTPException(status_code=500, detail=_safe_error(e))


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

    # App logo. Stored as .logo (not .png) because the deploy sync drops *.png
    # via .gitignore; served here with an explicit PNG media type so the browser
    # renders it in <img src="/bricktrace-logo.png">.
    _logo_path = os.path.join(static_dir, "bricktrace-logo.logo")

    @app.get("/bricktrace-logo.png")
    async def serve_logo():
        if os.path.isfile(_logo_path):
            return FileResponse(_logo_path, media_type="image/png",
                                headers={"Cache-Control": "public, max-age=86400"})
        return _index_response()

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
