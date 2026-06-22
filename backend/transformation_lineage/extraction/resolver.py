"""Resolve a Databricks job run to workspace paths or git coordinates (Jobs API)."""

from __future__ import annotations

import logging
from typing import Any

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.jobs import Run, RunTask

from transformation_lineage.types import ResolvedTaskSource

logger = logging.getLogger(__name__)


def _task_key(task: RunTask) -> str | None:
    return getattr(task, "task_key", None)


def _coerce_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _git_from_job_settings(client: WorkspaceClient, job_id: int) -> dict[str, Any]:
    try:
        job = client.jobs.get(job_id=job_id)
    except Exception as e:
        logger.warning("jobs.get failed for job_id=%s: %s", job_id, e)
        return {}
    settings = getattr(job, "settings", None)
    gs = getattr(settings, "git_source", None) if settings else None
    if not gs:
        return {}
    return {
        "git_url": getattr(gs, "git_url", None),
        "git_provider": getattr(gs, "git_provider", None),
        "git_branch": getattr(gs, "git_branch", None),
        "git_commit": getattr(gs, "git_commit", None),
    }


def _git_from_run(run: Run) -> dict[str, Any]:
    """Best-effort: some APIs expose a resolved snapshot on the run."""
    snap = getattr(run, "git_snapshot", None)
    if not snap:
        return {}
    return {
        "git_url": getattr(snap, "git_url", None),
        "git_provider": getattr(snap, "git_provider", None),
        "git_branch": getattr(snap, "git_branch", None),
        "git_commit": getattr(snap, "git_commit", None),
    }


def resolve_run_tasks(client: WorkspaceClient, run_id: int) -> list[ResolvedTaskSource]:
    """
    Expand a job run into task-level sources we can fetch as text (notebook / file).

    Handles notebook tasks in workspace or git-backed jobs by combining run + job settings.
    """
    run = client.jobs.get_run(run_id=run_id)
    job_id = _coerce_int(getattr(run, "job_id", None))
    git_ctx = _git_from_run(run)
    if job_id and not git_ctx.get("git_url"):
        git_ctx = {**git_ctx, **_git_from_job_settings(client, job_id)}

    tasks = list(getattr(run, "tasks", None) or [])
    resolved: list[ResolvedTaskSource] = []

    for task in tasks:
        nt = getattr(task, "notebook_task", None)
        if nt:
            path = getattr(nt, "notebook_path", None) or ""
            source = str(getattr(nt, "source", None) or "WORKSPACE").upper()
            if source == "GIT" or git_ctx.get("git_url"):
                resolved.append(
                    ResolvedTaskSource(
                        run_id=run_id,
                        job_id=job_id,
                        task_key=_task_key(task),
                        source_kind="git_file",
                        git_url=git_ctx.get("git_url"),
                        git_provider=git_ctx.get("git_provider"),
                        git_branch=git_ctx.get("git_branch"),
                        git_commit=git_ctx.get("git_commit"),
                        git_path=path,
                        language=_infer_language_from_path(path),
                    )
                )
            else:
                resolved.append(
                    ResolvedTaskSource(
                        run_id=run_id,
                        job_id=job_id,
                        task_key=_task_key(task),
                        source_kind="workspace_notebook",
                        workspace_path=path,
                        language=_infer_language_from_path(path),
                    )
                )
            continue

        spt = getattr(task, "spark_python_task", None)
        if spt:
            path = getattr(spt, "python_file", None)
            if path and (str(path).startswith("dbfs:") or str(path).startswith("/")):
                resolved.append(
                    ResolvedTaskSource(
                        run_id=run_id,
                        job_id=job_id,
                        task_key=_task_key(task),
                        source_kind="workspace_notebook",
                        workspace_path=str(path).replace("dbfs:", "/") if str(path).startswith("dbfs:") else str(path),
                        language="python",
                    )
                )
            continue

    if not resolved:
        logger.info("No notebook/python tasks resolved for run_id=%s", run_id)

    return resolved


def _infer_language_from_path(path: str | None) -> str | None:
    if not path:
        return None
    lower = path.lower()
    if lower.endswith(".sql"):
        return "sql"
    if lower.endswith(".py"):
        return "python"
    if lower.endswith(".ipynb") or "/notebooks/" in lower:
        return "python"
    return None
