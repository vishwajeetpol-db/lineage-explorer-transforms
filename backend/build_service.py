"""Build Service — submits and monitors serverless jobs that build transformation
lineage for a target table.

The heavy lifting (source extraction, SQL/PySpark parsing, graph construction,
Delta materialization) happens in the `run_all` notebook executed by the job.
This service just orchestrates: submit, poll, report status.

Design:
- Uses Databricks REST API for job submission (runs/submit one-time jobs)
- Uses Databricks SDK for status polling (get_run)
- No Streamlit dependencies — pure async-compatible functions called from FastAPI
- Thread-safe for concurrent polling from multiple users
"""

import os
import logging
import threading
from datetime import datetime

import requests as http_client
from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState

from backend.lineage_service import _get_client
from backend.models import BuildJobStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
# ---------------------------------------------------------------------------
# Pipeline notebook path resolution (A7 FIX)
#
# Resolution order:
#   a) PIPELINE_NOTEBOOK_PATH env var (strip whitespace; empty/whitespace = unset)
#   b) If unset AND DATABRICKS_APP_NAME is set: call apps.get to discover the
#      deployed source_code_path, normalize /Users or /Shared → /Workspace/...,
#      then append /notebooks/run_pipeline
#   c) Else return "" — callers must check is_build_configured() and fail closed
#
# A7 rule: NEVER derive from __file__ — container paths are meaningless.
# ---------------------------------------------------------------------------
_pipeline_notebook_path_cache: str | None = None  # lazy cache (None = not yet resolved)
_pipeline_path_lock = threading.Lock()


def _normalize_workspace_path(path: str) -> str:
    """Ensure a path starts with /Workspace.

    Databricks Apps may report source_code_path as /Users/... or /Shared/...
    (workspace-relative) — prepend /Workspace so it resolves for job submission.
    """
    path = path.rstrip("/")
    if path.startswith("/Workspace"):
        return path
    if path.startswith("/Users") or path.startswith("/Shared") or path.startswith("/Repos"):
        return f"/Workspace{path}"
    # Already absolute or unknown prefix — return as-is with a warning
    if not path.startswith("/"):
        logger.warning(f"source_code_path is not absolute: {path}")
    return path


def _discover_from_app_source() -> str:
    """Discover notebook path from the App's deployed source_code_path.

    Calls apps.get(DATABRICKS_APP_NAME) and walks common attribute locations
    for the source root. Returns "" if discovery fails (non-fatal).
    """
    app_name = os.environ.get("DATABRICKS_APP_NAME", "").strip()
    if not app_name:
        return ""
    try:
        client = _get_client()
        app_info = client.apps.get(app_name)

        # Try multiple paths the SDK may expose the source root
        source_root = None
        for attr in (
            "default_source_code_path",
            "source_code_path",
        ):
            val = getattr(app_info, attr, None)
            if val:
                source_root = val
                break

        # Check active/pending deployment
        if not source_root:
            for deploy_attr in ("active_deployment", "pending_deployment"):
                deploy = getattr(app_info, deploy_attr, None)
                if deploy:
                    val = getattr(deploy, "source_code_path", None)
                    if val:
                        source_root = val
                        break

        if not source_root:
            logger.info(
                f"App '{app_name}' found but no source_code_path in response. "
                "Set PIPELINE_NOTEBOOK_PATH explicitly."
            )
            return ""

        normalized = _normalize_workspace_path(source_root)
        notebook_path = f"{normalized}/notebooks/run_pipeline"
        logger.info(f"Discovered pipeline notebook path from App source: {notebook_path}")
        return notebook_path

    except Exception as e:
        logger.warning(
            f"Failed to discover pipeline path from App '{app_name}': {e}. "
            "Set PIPELINE_NOTEBOOK_PATH explicitly to enable builds."
        )
        return ""


def get_pipeline_notebook_path() -> str:
    """Return the resolved pipeline notebook path (lazy-cached).

    Resolution order:
      a) PIPELINE_NOTEBOOK_PATH env (strip whitespace; empty = unset)
      b) App source discovery (if DATABRICKS_APP_NAME is set)
      c) "" — fail closed
    """
    global _pipeline_notebook_path_cache
    if _pipeline_notebook_path_cache is not None:
        return _pipeline_notebook_path_cache

    with _pipeline_path_lock:
        # Double-check after acquiring lock
        if _pipeline_notebook_path_cache is not None:
            return _pipeline_notebook_path_cache

        # (a) Env var — strip whitespace; whitespace-only = unset
        raw = os.environ.get("PIPELINE_NOTEBOOK_PATH", "").strip()
        if raw:
            if not raw.startswith("/Workspace"):
                logger.warning(
                    f"PIPELINE_NOTEBOOK_PATH does not start with /Workspace: {raw}. "
                    "This may fail when submitting jobs."
                )
            _pipeline_notebook_path_cache = raw
            return _pipeline_notebook_path_cache

        # (b) Discover from App source
        discovered = _discover_from_app_source()
        if discovered:
            _pipeline_notebook_path_cache = discovered
            return _pipeline_notebook_path_cache

        # (c) Fail closed
        logger.info(
            "PIPELINE_NOTEBOOK_PATH not set and App source discovery unavailable. "
            "Transform build will be unavailable. "
            "Set it in databricks.yml env section to enable builds."
        )
        _pipeline_notebook_path_cache = ""
        return _pipeline_notebook_path_cache


def _reset_pipeline_notebook_path_cache() -> None:
    """Reset the lazy cache — for tests only."""
    global _pipeline_notebook_path_cache
    with _pipeline_path_lock:
        _pipeline_notebook_path_cache = None


WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")

# Build pipeline step names (for progress UI)
BUILD_STEPS = [
    "Validating Table",
    "Initializing Job",
    "Schema Discovery",
    "SQL Extraction",
    "Dependency Parsing",
    "Graph Construction",
    "Edge Materialization",
    "Cache Update",
]

# A12 FIX: Per-table build lock prevents concurrent duplicate Jobs for same FQN.
# Maps table_fqn → run_id of the currently in-progress build.
_build_locks: dict[str, str] = {}
_build_lock = threading.Lock()


def _estimate_step_from_progress(pct: int) -> int:
    """Map job progress percentage to a build step index."""
    if pct <= 5:
        return 0
    elif pct <= 10:
        return 1
    elif pct <= 25:
        return 2
    elif pct <= 40:
        return 3
    elif pct <= 55:
        return 4
    elif pct <= 70:
        return 5
    elif pct <= 90:
        return 6
    else:
        return 7


# ---------------------------------------------------------------------------
# Job Submission
# ---------------------------------------------------------------------------
def submit_build_job(
    target_table_fqn: str,
    target_catalog: str | None = None,
    target_schema: str | None = None,
    force_reparse: bool = False,
) -> str:
    """Submit a serverless one-time job to build transformation lineage.

    Returns the run_id as a string.
    Raises RuntimeError if PIPELINE_NOTEBOOK_PATH is not configured.
    """
    notebook_path = get_pipeline_notebook_path()
    if not notebook_path:
        raise RuntimeError(
            "PIPELINE_NOTEBOOK_PATH is not configured. Set it in databricks.yml "
            "(env section) to the workspace path of the run_all notebook."
        )

    # A12 FIX: Prevent concurrent builds for the same table
    with _build_lock:
        existing_run = _build_locks.get(target_table_fqn)
        if existing_run:
            logger.info(f"Build already in progress for {target_table_fqn}: run_id={existing_run}")
            raise RuntimeError(
                f"A build is already in progress for {target_table_fqn} (run_id={existing_run}). "
                "Wait for it to complete or check /api/transform/status/{run_id}."
            )

    client = _get_client()
    host = client.config.host.rstrip('/')
    headers = client.config.authenticate()

    run_name = (
        f"Lineage Builder - {target_table_fqn} - "
        f"{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    payload = {
        "run_name": run_name,
        "tasks": [{
            "task_key": "build_lineage",
            "notebook_task": {
                "notebook_path": notebook_path,
                "source": "WORKSPACE",
                "base_parameters": {
                    # Option A — single dedicated, app-SP-owned lineage store.
                    # Edges are ALWAYS written to LINEAGE_CATALOG.LINEAGE_SCHEMA,
                    # never to the selected table's data catalog (the app SP has
                    # no write there). KPI_TABLES (what to analyze) stays dynamic,
                    # driven by the table the user clicked Generate on.
                    "TARGET_CATALOG": LINEAGE_CATALOG,
                    "TARGET_SCHEMA": LINEAGE_SCHEMA,
                    "KPI_TABLES": target_table_fqn,
                    "BUILD_ONLY": "true",
                    # A forced/regenerate build must re-parse even if the source
                    # content is byte-identical (else change-detection skips it).
                    "FORCE_REPARSE": "true" if force_reparse else "false",
                    # Producer-discovery window (hours). Passed through so the
                    # build can discover producers that last ran a while ago
                    # instead of silently finding nothing. Kept in sync with the
                    # app's diagnose window via the same env var.
                    "DISCOVERY_LOOKBACK_HOURS": os.environ.get("DISCOVERY_LOOKBACK_HOURS", "8760"),
                },
            },
            "environment_key": "Default",
        }],
        "environments": [{
            "environment_key": "Default",
            "spec": {
                "client": "2",
                # Pin sqlparse: newer releases (0.5.x) tokenize/group multi-statement
                # SQL differently, which leaks alias resolution ACROSS statements in a
                # multi-CREATE notebook (e.g. gold.customer_orders.customer_id getting
                # spurious edges from raw_customers/raw_orders). 0.4.4 parses each
                # statement in isolation as the engine expects. See PARSER_VERSION.
                "dependencies": ["sqlparse==0.4.4", "requests", "databricks-sdk"],
            },
        }],
    }

    resp = http_client.post(
        f"{host}/api/2.1/jobs/runs/submit",
        json=payload,
        headers=headers,
        timeout=30,
    )
    resp.raise_for_status()
    run_id = str(resp.json()["run_id"])

    # A12 FIX: Register this build in the per-table lock
    with _build_lock:
        _build_locks[target_table_fqn] = run_id

    logger.info(f"Submitted build job for {target_table_fqn}: run_id={run_id}")
    return run_id


# ---------------------------------------------------------------------------
# Job Status Polling
# ---------------------------------------------------------------------------
_PROGRESS_MAP = {
    RunLifeCycleState.PENDING: 5,
    RunLifeCycleState.QUEUED: 10,
    RunLifeCycleState.RUNNING: 50,
    RunLifeCycleState.TERMINATING: 90,
    RunLifeCycleState.TERMINATED: 100,
    RunLifeCycleState.SKIPPED: 100,
    RunLifeCycleState.INTERNAL_ERROR: 100,
}

_TERMINAL_STATES = {
    RunLifeCycleState.TERMINATED,
    RunLifeCycleState.SKIPPED,
    RunLifeCycleState.INTERNAL_ERROR,
}


def get_build_status(run_id: str) -> BuildJobStatus:
    """Poll the status of a lineage build job.

    Returns a structured status object with progress info.
    """
    try:
        client = _get_client()
        run = client.jobs.get_run(run_id=int(run_id))
        state = run.state

        lc = state.life_cycle_state if state else None
        result = state.result_state if state else None

        progress = _PROGRESS_MAP.get(lc, 0)
        is_complete = lc in _TERMINAL_STATES
        is_success = (result == RunResultState.SUCCESS) if result else False

        # A12 FIX: Release per-table lock when build completes
        if is_complete:
            with _build_lock:
                # Remove any entry whose run_id matches this completed run
                to_remove = [k for k, v in _build_locks.items() if v == run_id]
                for k in to_remove:
                    del _build_locks[k]

        current_step = _estimate_step_from_progress(progress)

        return BuildJobStatus(
            run_id=run_id,
            state=lc.value if lc else "UNKNOWN",
            result_state=result.value if result else None,
            state_message=(state.state_message if state else "") or "",
            progress_pct=progress,
            is_complete=is_complete,
            is_success=is_success,
            current_step=current_step,
            current_step_name=BUILD_STEPS[current_step] if current_step < len(BUILD_STEPS) else "Done",
            total_steps=len(BUILD_STEPS),
            steps=BUILD_STEPS,
            run_page_url=run.run_page_url or "",
        )

    except Exception as e:
        logger.error(f"Failed to get build status for run_id={run_id}: {e}")
        return BuildJobStatus(
            run_id=run_id,
            state="ERROR",
            result_state=None,
            state_message=str(e),
            progress_pct=0,
            is_complete=True,
            is_success=False,
            current_step=0,
            current_step_name="Error",
            total_steps=len(BUILD_STEPS),
            steps=BUILD_STEPS,
            run_page_url="",
        )


def is_build_configured() -> bool:
    """Check if the build pipeline notebook path is configured."""
    return bool(get_pipeline_notebook_path())
