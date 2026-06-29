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
# If PIPELINE_NOTEBOOK_PATH is set, use it directly.
# Otherwise, derive from the app's own deployed location (notebooks/run_pipeline).
_raw_notebook_path = os.environ.get("PIPELINE_NOTEBOOK_PATH", "")


def _derive_pipeline_notebook_path() -> str:
    """Auto-derive the pipeline notebook path from the app's deployment location.

    The app deploys to /Workspace/Users/<user>/<app-folder>/ and our
    run_pipeline notebook lives at notebooks/run_pipeline relative to it.
    Falls back to env-configured path or raises if nothing resolves.
    """
    if _raw_notebook_path:
        return _raw_notebook_path
    # Derive from __file__ — this module lives at <app_root>/backend/build_service.py
    this_dir = os.path.dirname(os.path.abspath(__file__))
    app_root = os.path.dirname(this_dir)  # go up from backend/
    candidate = os.path.join(app_root, "notebooks", "run_pipeline")
    # Databricks workspace paths start with /Workspace — normalise
    if not candidate.startswith("/Workspace"):
        candidate = "/Workspace" + candidate if candidate.startswith("/") else candidate
    return candidate


PIPELINE_NOTEBOOK_PATH = _derive_pipeline_notebook_path()
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
    if not PIPELINE_NOTEBOOK_PATH:
        raise RuntimeError(
            "PIPELINE_NOTEBOOK_PATH is not configured. Set it in databricks.yml "
            "(env section) to the workspace path of the run_all notebook."
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
                "notebook_path": PIPELINE_NOTEBOOK_PATH,
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
    return bool(PIPELINE_NOTEBOOK_PATH)
