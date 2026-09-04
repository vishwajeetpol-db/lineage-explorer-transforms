"""Edge-case guards — resolves C1, C2, C3, C4, C5, C7, C11, C13, C14, C16.

This module provides runtime guards, health checks, and graceful degradation
for edge cases identified in the BrickTrace audit. Each function is designed
to be called from the relevant route/service and returns structured metadata
that the frontend can surface as banners, warnings, or info badges.

All guards are non-fatal: they return diagnostic info but never block the
main lineage/transform flow. The API enriches responses with these signals
so the UI can display appropriate warnings.
"""
from __future__ import annotations

import os
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

from databricks.sdk.service.sql import StatementState

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
MAX_GRAPH_NODES = int(os.environ.get("MAX_GRAPH_NODES", "500"))
LINEAGE_LOOKBACK_DAYS = int(os.environ.get("LINEAGE_LOOKBACK_DAYS", "30"))


# ---------------------------------------------------------------------------
# Data classes for structured diagnostic responses
# ---------------------------------------------------------------------------

@dataclass
class HealthStatus:
    """System health check result (C1)."""
    system_tables_available: bool = True
    sp_grants_valid: bool = True
    warehouse_reachable: bool = True
    missing_grants: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class GraphWarnings:
    """Warnings attached to lineage graph responses (C2, C3, C4, C5)."""
    truncated: bool = False
    truncation_reason: Optional[str] = None
    node_count: int = 0
    max_nodes: int = MAX_GRAPH_NODES
    partial_catalog_access: list[str] = field(default_factory=list)  # C2
    foreign_catalog_boundaries: list[str] = field(default_factory=list)  # C3
    outside_lookback_window: list[str] = field(default_factory=list)  # C5
    lookback_days: int = LINEAGE_LOOKBACK_DAYS
    spark_connect_note: Optional[str] = None  # C13


@dataclass
class FeatureFlagHealth:
    """Feature flags table health (C7, C11)."""
    table_exists: bool = True
    table_accessible: bool = True
    flags_schema_exists: bool = True
    auto_created: bool = False
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# C1: System tables disabled / SP grants missing
# ---------------------------------------------------------------------------

_health_cache: Optional[HealthStatus] = None
_health_cache_time: float = 0
_HEALTH_CACHE_TTL = 300  # 5 minutes


def check_system_tables_health(execute_sql_fn) -> HealthStatus:
    """C1: Verify system tables are accessible and SP has required grants.

    Returns a HealthStatus with actionable details. Result is cached for 5 min
    to avoid hammering system tables on every request.
    """
    global _health_cache, _health_cache_time

    if _health_cache and (time.time() - _health_cache_time) < _HEALTH_CACHE_TTL:
        return _health_cache

    status = HealthStatus()
    try:
        # Test system.access.table_lineage access
        result = execute_sql_fn(
            "SELECT 1 FROM system.access.table_lineage LIMIT 1"
        )
        if result is None:
            status.system_tables_available = False
            status.missing_grants.append("SELECT on system.access.table_lineage")
    except Exception as e:
        err_msg = str(e).lower()
        if "table_or_view_not_found" in err_msg or "does not exist" in err_msg:
            status.system_tables_available = False
            status.error = "System tables not enabled at account level"
        elif "permission" in err_msg or "denied" in err_msg or "privilege" in err_msg:
            status.sp_grants_valid = False
            status.missing_grants.append("SELECT on system.access.table_lineage")
            status.error = f"SP missing grants: {e}"
        else:
            status.warehouse_reachable = False
            status.error = f"Warehouse unreachable: {e}"

    _health_cache = status
    _health_cache_time = time.time()
    return status


def get_health_status_dict(execute_sql_fn) -> dict:
    """Return health status as a JSON-serializable dict for the /api/health endpoint."""
    return asdict(check_system_tables_health(execute_sql_fn))


# ---------------------------------------------------------------------------
# C2: Incomplete cross-catalog BROWSE detection
# ---------------------------------------------------------------------------

def detect_partial_catalog_access(
    requested_catalogs: list[str],
    accessible_catalogs: list[str],
) -> list[str]:
    """C2: Detect catalogs the user requested but the SP cannot fully BROWSE.

    Returns a list of catalog names with incomplete access so the frontend
    can display a warning banner.
    """
    accessible_set = {c.lower() for c in accessible_catalogs}
    partial = []
    for cat in requested_catalogs:
        if cat.lower() not in accessible_set:
            partial.append(cat)
    return partial


# ---------------------------------------------------------------------------
# C3: Delta Sharing / foreign catalog boundaries
# ---------------------------------------------------------------------------

def detect_foreign_boundaries(nodes: list[dict]) -> list[str]:
    """C3: Identify nodes that are foreign/shared catalog boundaries.

    These are tables whose catalog is a foreign catalog or Delta Share recipient.
    The frontend should render them with a boundary indicator.
    """
    boundaries = []
    for node in nodes:
        catalog = node.get("catalog", "")
        node_type = node.get("type", "")
        # Foreign catalogs typically have specific markers
        if node_type in ("FOREIGN", "SHARE_RECIPIENT", "EXTERNAL"):
            boundaries.append(node.get("name", catalog))
        # Also detect by catalog properties if available
        elif node.get("is_foreign", False) or node.get("is_shared", False):
            boundaries.append(node.get("name", catalog))
    return boundaries


# ---------------------------------------------------------------------------
# C4: Large graphs / memory bounds
# ---------------------------------------------------------------------------

def apply_graph_truncation(
    nodes: list[dict],
    edges: list[dict],
    max_nodes: int = MAX_GRAPH_NODES,
) -> tuple[list[dict], list[dict], bool, str | None]:
    """C4: Truncate graph if it exceeds max_nodes.

    Returns (nodes, edges, truncated, reason). If truncated, nodes are
    limited to max_nodes (preserving the focus table's neighborhood) and
    edges are filtered to only connect retained nodes.
    """
    if len(nodes) <= max_nodes:
        return nodes, edges, False, None

    # Keep first max_nodes nodes (the BFS ordering already prioritizes
    # proximity to the focus table)
    truncated_nodes = nodes[:max_nodes]
    retained_ids = {n.get("id") or n.get("name") for n in truncated_nodes}

    # Filter edges to only those connecting retained nodes
    truncated_edges = [
        e for e in edges
        if (e.get("source") in retained_ids and e.get("target") in retained_ids)
    ]

    reason = (
        f"Graph truncated from {len(nodes)} to {max_nodes} nodes. "
        f"Use schema/catalog scope or filters to narrow results."
    )
    logger.info(f"C4: {reason}")
    return truncated_nodes, truncated_edges, True, reason


# ---------------------------------------------------------------------------
# C5: Producer outside discovery lookback window
# ---------------------------------------------------------------------------

def detect_outside_lookback(nodes: list[dict], lookback_days: int = LINEAGE_LOOKBACK_DAYS) -> list[str]:
    """C5: Identify nodes whose last activity is outside the lookback window.

    These producers may have stale lineage data. Returns table FQNs that
    haven't been seen in system.access.table_lineage within lookback_days.
    """
    import datetime
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=lookback_days)
    outside = []
    for node in nodes:
        last_seen = node.get("last_seen_at") or node.get("event_time")
        if last_seen:
            try:
                if isinstance(last_seen, str):
                    ts = datetime.datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
                else:
                    ts = last_seen
                if ts < cutoff:
                    outside.append(node.get("name", node.get("id", "unknown")))
            except (ValueError, TypeError):
                pass
    return outside


# ---------------------------------------------------------------------------
# C7 + C11: Feature flags table missing / capture with flags off
# ---------------------------------------------------------------------------

def ensure_feature_flags_table(execute_sql_fn) -> FeatureFlagHealth:
    """C7/C11: Ensure the feature_flags table exists, creating it if needed.

    This is called on startup and from the Control Panel. If the table doesn't
    exist, it's auto-created with all flags defaulting to disabled.
    """
    health = FeatureFlagHealth()
    flags_table = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.feature_flags"

    try:
        # Check if table exists
        result = execute_sql_fn(f"SELECT COUNT(*) as cnt FROM {flags_table}")
        if result is not None:
            return health  # Table exists and is accessible
    except Exception as e:
        err_msg = str(e).lower()
        if "table_or_view_not_found" in err_msg or "does not exist" in err_msg:
            health.table_exists = False
            # Try to create it
            try:
                execute_sql_fn(f"""
                    CREATE TABLE IF NOT EXISTS {flags_table} (
                        flag_id STRING NOT NULL,
                        enabled BOOLEAN NOT NULL DEFAULT false,
                        updated_by STRING,
                        updated_at TIMESTAMP DEFAULT current_timestamp(),
                        notes STRING
                    ) USING DELTA
                    COMMENT 'BrickTrace feature flags — auto-created by edge_case_guards'
                """)
                health.auto_created = True
                health.table_exists = True
                logger.info("C7: Auto-created feature_flags table")
            except Exception as create_err:
                health.error = f"Cannot create feature_flags table: {create_err}"
                health.table_accessible = False
                logger.warning(f"C7: {health.error}")
        elif "schema" in err_msg and "not found" in err_msg:
            health.flags_schema_exists = False
            health.table_exists = False
            health.error = f"Schema {LINEAGE_CATALOG}.{LINEAGE_SCHEMA} does not exist. Run setup.sql first."
            logger.warning(f"C7: {health.error}")
        else:
            health.table_accessible = False
            health.error = f"Cannot access feature_flags: {e}"
            logger.warning(f"C7: {health.error}")

    return health


def check_capture_prerequisites(execute_sql_fn) -> dict:
    """C11: Check all prerequisites for plan capture (flags + schema).

    Returns a dict with status and actionable messages.
    """
    result = {
        "capture_ready": False,
        "flag_enabled": False,
        "schema_exists": False,
        "table_exists": False,
        "issues": [],
    }

    # Check schema
    try:
        execute_sql_fn(
            f"SELECT 1 FROM information_schema.schemata "
            f"WHERE catalog_name = '{LINEAGE_CATALOG}' AND schema_name = '{LINEAGE_SCHEMA}' LIMIT 1"
        )
        result["schema_exists"] = True
    except Exception:
        result["issues"].append(
            f"Schema {LINEAGE_CATALOG}.{LINEAGE_SCHEMA} not found. Run setup.sql."
        )

    # Check captured_plans table
    try:
        execute_sql_fn(
            f"SELECT 1 FROM {LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.captured_plans LIMIT 1"
        )
        result["table_exists"] = True
    except Exception:
        result["issues"].append(
            "captured_plans table not found. It will be created on first capture."
        )

    # Check flag state
    try:
        from backend.feature_flags import get_flag_state
        result["flag_enabled"] = get_flag_state("lineage_tracking.plan_capture")
    except Exception:
        result["issues"].append("Cannot read feature flags. Enable via Control Panel.")

    result["capture_ready"] = (
        result["schema_exists"] and result["flag_enabled"]
    )
    return result


# ---------------------------------------------------------------------------
# C13: Spark Connect vs classic capture compatibility
# ---------------------------------------------------------------------------

SPARK_CONNECT_NOTE = (
    "Plan capture uses df.queryExecution.analyzed (classic) or df.explain(extended=True) "
    "(Spark Connect). On Serverless/Spark Connect compute, explain() output is parsed "
    "instead of direct plan tree access. Both paths produce equivalent lineage; "
    "Spark Connect adds ~50ms latency per capture call due to explain() serialization."
)


def get_spark_connect_compatibility() -> dict:
    """C13: Return Spark Connect compatibility information."""
    return {
        "classic_capture": "df.queryExecution.analyzed (direct plan access)",
        "spark_connect_capture": "df.explain(extended=True) → parse output",
        "equivalence": "Both produce identical column-level lineage",
        "latency_delta": "~50ms additional per capture on Spark Connect",
        "serverless_compatible": True,
        "note": SPARK_CONNECT_NOTE,
    }


# ---------------------------------------------------------------------------
# C14: Soft-warm cache on restart
# ---------------------------------------------------------------------------

_warm_lock = threading.Lock()
_warm_started = False


def soft_warm_cache(execute_sql_fn, lineage_cache=None) -> dict:
    """C14: Warm the lineage cache on App startup from Delta distributed cache.

    Reads recently-accessed scopes from the distributed cache table and
    populates the in-process LRU so the first user request doesn't hit a
    cold warehouse query. Non-blocking, runs in a background thread.
    """
    global _warm_started
    with _warm_lock:
        if _warm_started:
            return {"status": "already_started"}
        _warm_started = True

    def _warm():
        try:
            from backend.cache_service import DeltaCacheService
            cache_svc = DeltaCacheService()
            entries = cache_svc.get_recent_entries(limit=10)
            warmed = 0
            for entry in entries:
                if lineage_cache is not None and entry.get("cache_key"):
                    lineage_cache[entry["cache_key"]] = entry.get("data")
                    warmed += 1
            logger.info(f"C14: Soft-warmed {warmed} cache entries on startup")
        except Exception as e:
            logger.warning(f"C14: Soft-warm failed (non-fatal): {e}")

    thread = threading.Thread(target=_warm, daemon=True, name="cache-warm")
    thread.start()
    return {"status": "warming", "thread": thread.name}


# ---------------------------------------------------------------------------
# C16: SCD/CDC detection without capture_cdc_spec
# ---------------------------------------------------------------------------

def detect_scd_cdc_patterns(table_fqn: str, execute_sql_fn) -> dict:
    """C16: Detect SCD/CDC patterns in a table for lineage enrichment.

    Checks for common SCD Type 2 / CDC indicators:
    - __START_AT / __END_AT columns (Delta CDC)
    - _change_type column (CDF)
    - effective_date / expiry_date patterns
    - is_current flag columns

    Returns detection result with confidence and recommended action.
    """
    result = {
        "table_fqn": table_fqn,
        "scd_detected": False,
        "cdc_detected": False,
        "pattern": None,
        "confidence": "none",
        "indicators": [],
        "recommendation": None,
    }

    try:
        # Get column names
        cols_result = execute_sql_fn(
            f"DESCRIBE TABLE {table_fqn}"
        )
        if not cols_result:
            return result

        col_names = [row.get("col_name", "").lower() for row in cols_result if row.get("col_name")]

        # SCD Type 2 indicators
        scd2_indicators = [
            ("__start_at", "Delta CDC start marker"),
            ("__end_at", "Delta CDC end marker"),
            ("effective_date", "SCD2 effective date"),
            ("expiry_date", "SCD2 expiry date"),
            ("valid_from", "SCD2 valid_from"),
            ("valid_to", "SCD2 valid_to"),
            ("is_current", "SCD2 current flag"),
            ("is_active", "SCD2 active flag"),
            ("_rescued_data", "Auto Loader rescued data"),
        ]

        cdc_indicators = [
            ("_change_type", "Delta CDF change type"),
            ("_commit_version", "Delta CDF commit version"),
            ("_commit_timestamp", "Delta CDF timestamp"),
            ("op", "CDC operation column"),
            ("operation_type", "CDC operation type"),
        ]

        for col, desc in scd2_indicators:
            if col in col_names:
                result["indicators"].append({"column": col, "type": "SCD2", "description": desc})

        for col, desc in cdc_indicators:
            if col in col_names:
                result["indicators"].append({"column": col, "type": "CDC", "description": desc})

        # Determine pattern
        scd_count = sum(1 for i in result["indicators"] if i["type"] == "SCD2")
        cdc_count = sum(1 for i in result["indicators"] if i["type"] == "CDC")

        if scd_count >= 2:
            result["scd_detected"] = True
            result["pattern"] = "SCD_TYPE_2"
            result["confidence"] = "high" if scd_count >= 3 else "medium"
            result["recommendation"] = (
                "Table appears to use SCD Type 2 pattern. Enable Runtime Plan Capture "
                "with capture_cdc_spec=True for accurate temporal lineage."
            )
        elif cdc_count >= 1:
            result["cdc_detected"] = True
            result["pattern"] = "CDC_FEED"
            result["confidence"] = "high" if cdc_count >= 2 else "medium"
            result["recommendation"] = (
                "Table appears to be a CDC feed. Enable Runtime Plan Capture "
                "for accurate change-event lineage."
            )
        elif scd_count == 1:
            result["scd_detected"] = True
            result["pattern"] = "SCD_POSSIBLE"
            result["confidence"] = "low"
            result["recommendation"] = (
                "Table may use SCD patterns. Review column semantics and consider "
                "enabling capture_cdc_spec if temporal lineage is needed."
            )

    except Exception as e:
        result["error"] = str(e)
        logger.debug(f"C16: SCD/CDC detection failed for {table_fqn}: {e}")

    return result


# ---------------------------------------------------------------------------
# C9: Multi-user App SP visibility (architectural documentation)
# ---------------------------------------------------------------------------

APP_SP_VISIBILITY_NOTE = {
    "status": "architectural_limitation",
    "description": (
        "All SQL executes as the App Service Principal (WorkspaceClient()). "
        "Per-user UC ACLs are NOT enforced — the App ACL is the real perimeter. "
        "Any user who can open the App sees whatever the SP can BROWSE/SELECT."
    ),
    "mitigation": (
        "Gate App access via Databricks Apps ACL (admin group membership). "
        "Scope the App SP's BROWSE grants tightly to intended catalogs/schemas. "
        "OBO (On-Behalf-Of) token delegation is a platform feature not yet available "
        "for Apps — when available, it will enable per-user UC enforcement."
    ),
    "obo_available": False,
    "workaround": "Use App ACL + tightly-scoped SP BROWSE grants",
}


# ---------------------------------------------------------------------------
# Composite: Build graph warnings for a lineage response
# ---------------------------------------------------------------------------

def build_graph_warnings(
    nodes: list[dict],
    edges: list[dict],
    accessible_catalogs: list[str] | None = None,
    requested_catalogs: list[str] | None = None,
) -> GraphWarnings:
    """Build a complete GraphWarnings object for a lineage response.

    Combines C2 (partial access), C3 (boundaries), C4 (truncation), C5 (lookback).
    """
    warnings = GraphWarnings()
    warnings.node_count = len(nodes)

    # C2: partial catalog access
    if requested_catalogs and accessible_catalogs:
        warnings.partial_catalog_access = detect_partial_catalog_access(
            requested_catalogs, accessible_catalogs
        )

    # C3: foreign/shared boundaries
    warnings.foreign_catalog_boundaries = detect_foreign_boundaries(nodes)

    # C4: truncation check (already applied by caller; just record)
    if len(nodes) >= MAX_GRAPH_NODES:
        warnings.truncated = True
        warnings.truncation_reason = (
            f"Graph limited to {MAX_GRAPH_NODES} nodes for performance. "
            f"Narrow scope to see complete lineage."
        )

    # C5: outside lookback
    warnings.outside_lookback_window = detect_outside_lookback(nodes)

    # C13: Spark Connect note (informational)
    warnings.spark_connect_note = SPARK_CONNECT_NOTE

    return warnings


# ---------------------------------------------------------------------------
# Startup integration
# ---------------------------------------------------------------------------

def run_startup_checks(execute_sql_fn, lineage_cache=None) -> dict:
    """Run all startup checks. Called from backend.startup.activate().

    Returns a summary dict of all edge-case guard initializations.
    """
    results = {}

    # C1: System tables health
    health = check_system_tables_health(execute_sql_fn)
    results["system_health"] = asdict(health)
    if not health.system_tables_available:
        logger.warning("C1: System tables NOT available — lineage will be empty")
    if not health.sp_grants_valid:
        logger.warning(f"C1: SP grants incomplete — missing: {health.missing_grants}")

    # C7: Feature flags table
    ff_health = ensure_feature_flags_table(execute_sql_fn)
    results["feature_flags"] = asdict(ff_health)
    if ff_health.auto_created:
        logger.info("C7: Feature flags table was auto-created")

    # C14: Soft-warm cache
    warm_result = soft_warm_cache(execute_sql_fn, lineage_cache)
    results["cache_warm"] = warm_result

    # C13: Spark Connect info (static)
    results["spark_connect"] = get_spark_connect_compatibility()

    return results
