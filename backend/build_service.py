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
import time
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


# ---------------------------------------------------------------------------
# Build source reachability
#
# `bundle deploy` uploads the app source into the DEPLOYING identity's home
# (/Workspace/Users/<deployer>/.bundle/<bundle>/<target>/files) — a folder only
# that identity and workspace admins can touch. The build job runs
# <source>/notebooks/run_pipeline as the APP's service principal, so unless that
# SP was granted CAN_RUN on the folder the run dies on its first task with
# "Unable to access the notebook ... lacks the required permissions" — about a
# minute of serverless compute spent to learn a permission is missing.
#
# Preflighting turns that into an instant, actionable error. Note the limit of
# what the probe can prove: get_status succeeds with CAN_READ, while executing a
# notebook_task needs CAN_RUN, and the app SP cannot read its own ACL. So the
# probe catches the common "no access at all" case, and _is_source_access_failure
# below covers the residual CAN_READ-only case from the job's own message.
# ---------------------------------------------------------------------------
_SOURCE_ACCESS_HINT = (
    "The app's service principal cannot reach the deployed build notebook, so no build job "
    "was submitted. `bundle deploy` uploads the source into the deploying identity's home "
    "folder, which the app's service principal has no permission on. Fix it from the deploy "
    "machine: ./grant_build_source_access.sh --profile <cli-profile> --app <app-name>"
)

_JOB_SOURCE_ACCESS_HINT = (
    " — this means the app's service principal lacks CAN_RUN on the deployed source folder. "
    "Run ./grant_build_source_access.sh from the deploy machine, then Regenerate."
)

# The path last proven reachable, and how long that proof is trusted.
#
# Latched on SUCCESS ONLY, so a transient API failure can never permanently disable
# builds and a grant applied while the app is running takes effect without a
# restart. The latch also EXPIRES, which is the other half of the same argument: a
# grant REVOKED under a long-lived app (a security sweep, a re-grant to the wrong
# SP, or a `bundle destroy`/redeploy that re-creates the folder with a fresh ACL at
# the same path) would otherwise be undetectable for the process lifetime, and
# every build would go back to dying a minute in on serverless compute — the exact
# cost this preflight exists to avoid.
#
# One path per process (get_pipeline_notebook_path resolves and caches a single
# value), so this is a scalar, not a collection. Guarded by an explicit lock to
# match _pipeline_notebook_path_cache and _build_locks rather than relying on the
# GIL, so all three caches in this module read the same way.
_SOURCE_ACCESS_TTL_SECONDS = 300.0
_source_access_ok_path: str | None = None
_source_access_ok_until: float = 0.0
_source_access_lock = threading.Lock()


class BuildSourceAccessError(RuntimeError):
    """The app SP cannot reach the build notebook — no job was submitted.

    Distinct from a generic RuntimeError so the API layer can return the curated
    remediation text instead of a truncated, sanitized error string.
    """


def _assert_source_readable(client, notebook_path: str) -> None:
    """Raise BuildSourceAccessError if the app SP cannot see the build notebook.

    A proven path is not re-probed until the latch expires
    (_SOURCE_ACCESS_TTL_SECONDS), so the common case costs nothing; a revoked grant
    is picked up on the next probe after that.
    """
    global _source_access_ok_path, _source_access_ok_until

    with _source_access_lock:
        if notebook_path == _source_access_ok_path and time.monotonic() < _source_access_ok_until:
            return
    try:
        client.workspace.get_status(notebook_path)
    except Exception as e:
        logger.error(
            f"Build source preflight failed for {notebook_path}: {e}. The app service "
            "principal needs CAN_RUN on the deployed source folder — run "
            "grant_build_source_access.sh."
        )
        raise BuildSourceAccessError(_SOURCE_ACCESS_HINT) from e
    with _source_access_lock:
        _source_access_ok_path = notebook_path
        _source_access_ok_until = time.monotonic() + _SOURCE_ACCESS_TTL_SECONDS


def _reset_source_access_cache() -> None:
    """Reset the reachability latch — for tests only."""
    global _source_access_ok_path, _source_access_ok_until
    with _source_access_lock:
        _source_access_ok_path = None
        _source_access_ok_until = 0.0


def _is_source_access_failure(message: str) -> bool:
    """True if a job's failure message is the source-permission failure.

    Deliberately narrow. The remediation this gates (_JOB_SOURCE_ACCESS_HINT) names
    one specific cause and one specific fix, with no hedging, so a false positive
    sends the operator to a script that cannot help. The second branch therefore
    keys on the platform's own identity clause ("the identity used to run this job,
    <sp>, lacks the required permissions") rather than a bare "notebook" mention,
    which matched any notebook-permission failure anywhere in the run.
    """
    m = (message or "").lower()
    if "unable to access the notebook" in m:
        return True
    return "lacks the required permissions" in m and "identity used to run this job" in m


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
# Maps table_fqn → run_id of the currently in-progress build, or _BUILD_RESERVED
# while a submit is in flight and its run_id is not known yet. Not a valid run_id,
# so get_build_status's "release the lock whose value matches this run" never
# matches it — the submit path is responsible for clearing its own reservation.
_BUILD_RESERVED = "\x00reserved"
_build_locks: dict[str, str] = {}
_build_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Build budget
#
# The admin gate on POST /api/transform/build stops non-admins spending money; it
# does nothing about an admin spending it. Every submission launches a serverless
# job, the per-table lock only stops DUPLICATE builds of the SAME table, and an
# admin working down a 200-table schema submits 200 legitimately-distinct runs as
# fast as the UI allows. "Admin" in an enterprise is a group, not one careful
# person, and the bill arrives with no owner attached.
#
# Two ceilings, both per process (see the C4 caveat in the enterprise review — with
# multiple replicas these multiply, and the fix is the same shared store the cache
# needs):
#   * MAX_BUILDS_IN_FLIGHT — concurrency, so a burst cannot fan out
#   * MAX_BUILDS_PER_DAY   — total spend, so a slow leak cannot run all week
#
# Both are deliberately generous: this is a guard rail against a runaway loop, not
# a workflow restriction. Exceeding one is a 429, not a 403 — it is capacity, not
# permission.
# ---------------------------------------------------------------------------
MAX_BUILDS_IN_FLIGHT = int(os.environ.get("MAX_BUILDS_IN_FLIGHT", "5"))
MAX_BUILDS_PER_DAY = int(os.environ.get("MAX_BUILDS_PER_DAY", "200"))

_budget_lock = threading.Lock()
_builds_today = 0
_budget_day: str = ""
# Who submitted what, so serverless spend has a name. Bounded ring, newest last.
_build_submitters: list[tuple[str, str, str]] = []   # (iso_ts, actor, table_fqn)
_BUILD_LOG_MAX = 500


class BuildBudgetError(RuntimeError):
    """A build budget ceiling was hit — the caller should back off, not retry now.

    Distinct from BuildSourceAccessError (a broken deploy) and from a bare
    RuntimeError (a defect) so the API layer can answer 429 rather than 500.
    """


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _reserve_budget(actor: str, table_fqn: str) -> None:
    """Charge one build against the budgets, or raise BuildBudgetError."""
    global _builds_today, _budget_day
    with _budget_lock:
        today = _today()
        if _budget_day != today:
            _budget_day = today
            _builds_today = 0
        if _builds_today >= MAX_BUILDS_PER_DAY:
            raise BuildBudgetError(
                f"The daily build budget of {MAX_BUILDS_PER_DAY} serverless builds is "
                f"spent. It resets at midnight. Raise MAX_BUILDS_PER_DAY if this "
                f"workspace genuinely needs more."
            )
        _builds_today += 1
        _build_submitters.append((datetime.now().isoformat(timespec="seconds"), actor or "unknown", table_fqn))
        del _build_submitters[:-_BUILD_LOG_MAX]


def _refund_budget() -> None:
    """Give back a charge whose build never launched."""
    global _builds_today
    with _budget_lock:
        if _builds_today > 0:
            _builds_today -= 1
        if _build_submitters:
            _build_submitters.pop()


def get_build_budget() -> dict:
    """Budget state, for /api/diagnostics and the admin dashboard."""
    with _budget_lock:
        used, day = _builds_today, _budget_day or _today()
        recent = list(_build_submitters[-20:])
    with _build_lock:
        in_flight = len(_build_locks)
    return {
        "day": day,
        "builds_today": used,
        "max_per_day": MAX_BUILDS_PER_DAY,
        "remaining_today": max(0, MAX_BUILDS_PER_DAY - used),
        "in_flight": in_flight,
        "max_in_flight": MAX_BUILDS_IN_FLIGHT,
        "recent_submissions": [
            {"at": t, "actor": a, "table_fqn": f} for t, a, f in reversed(recent)
        ],
    }


def _reset_build_budget() -> None:
    """Zero the budget — for tests only."""
    global _builds_today, _budget_day
    with _budget_lock:
        _builds_today = 0
        _budget_day = ""
        _build_submitters.clear()


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
    actor: str = "",
) -> str:
    """Submit a serverless one-time job to build transformation lineage.

    Returns the run_id as a string.

    `actor` is the submitting admin's email, recorded against the run so serverless
    spend has a name. Optional so existing callers keep working; the API layer
    passes it.

    Raises RuntimeError if PIPELINE_NOTEBOOK_PATH is not configured,
    BuildSourceAccessError if the app SP cannot reach the notebook, and
    BuildBudgetError if a build ceiling is hit.
    """
    notebook_path = get_pipeline_notebook_path()
    if not notebook_path:
        raise RuntimeError(
            "PIPELINE_NOTEBOOK_PATH is not configured. Set it in databricks.yml "
            "(env section) to the workspace path of the run_all notebook."
        )

    # A12 FIX: Prevent concurrent builds for the same table.
    #
    # RESERVE the slot inside the same critical section that checks it. Checking
    # here but only writing the run_id after the submit returned left a window
    # spanning a workspace round-trip (the preflight) plus the runs/submit POST —
    # wide enough for two requests for the same table to both find no lock, both
    # submit, and both bill a serverless run, with only the second run_id surviving
    # in _build_locks.
    with _build_lock:
        existing_run = _build_locks.get(target_table_fqn)
        if not existing_run and len(_build_locks) >= MAX_BUILDS_IN_FLIGHT:
            raise BuildBudgetError(
                f"{len(_build_locks)} builds are already running (limit "
                f"{MAX_BUILDS_IN_FLIGHT}). Each one is a serverless job — wait for "
                f"some to finish, or raise MAX_BUILDS_IN_FLIGHT."
            )
        if existing_run:
            logger.info(f"Build already in progress for {target_table_fqn}: run_id={existing_run}")
            in_flight = (
                "is being submitted right now"
                if existing_run == _BUILD_RESERVED
                else f"is already in progress (run_id={existing_run})"
            )
            raise RuntimeError(
                f"A build for {target_table_fqn} {in_flight}. "
                "Wait for it to complete or check /api/transform/status/{run_id}."
            )
        _build_locks[target_table_fqn] = _BUILD_RESERVED

    # Charged AFTER the reservation succeeds, so a duplicate request rejected above
    # never consumes budget, and refunded below if the submit itself fails.
    try:
        _reserve_budget(actor, target_table_fqn)
    except BuildBudgetError:
        with _build_lock:
            if _build_locks.get(target_table_fqn) == _BUILD_RESERVED:
                del _build_locks[target_table_fqn]
        raise

    try:
        return _submit_reserved_build_job(target_table_fqn, notebook_path, force_reparse)
    except BaseException:
        _refund_budget()
        # Nothing is running under this reservation — drop it, or the table stays
        # locked out until the process restarts. Only ever clears OUR placeholder:
        # once the real run_id is in place, releasing it is get_build_status's job.
        with _build_lock:
            if _build_locks.get(target_table_fqn) == _BUILD_RESERVED:
                del _build_locks[target_table_fqn]
        raise


def _submit_reserved_build_job(
    target_table_fqn: str,
    notebook_path: str,
    force_reparse: bool,
) -> str:
    """Preflight + submit, with this table's build slot already reserved.

    Split out so the reservation has exactly one release path (the caller's
    except); every failure below reaches it.
    """
    client = _get_client()

    # Fail fast and for free when the app SP cannot reach the notebook, instead
    # of paying for a serverless run that dies on its first task.
    _assert_source_readable(client, notebook_path)

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

    # A12 FIX: swap the reservation for the real run_id, which get_build_status
    # matches on to release the lock when the run completes.
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
        message = (state.state_message if state else "") or ""

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

        # A build that failed because the app SP cannot run the deployed notebook
        # says so in platform terms only ("Unable to access the notebook ...").
        # Append what to actually do about it — this is the message the panel shows.
        if is_complete and not is_success and _is_source_access_failure(message):
            message = f"{message}{_JOB_SOURCE_ACCESS_HINT}"

        current_step = _estimate_step_from_progress(progress)

        return BuildJobStatus(
            run_id=run_id,
            state=lc.value if lc else "UNKNOWN",
            result_state=result.value if result else None,
            state_message=message,
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
