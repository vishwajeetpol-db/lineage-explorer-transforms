"""Producer entity resolution — capability 25.

Resolves (entity_type, entity_id) pairs — as they appear in
system.access.table_lineage — to human-readable display names and
direct deep-links into the Databricks UI. Results are LRU-cached in
process to avoid repeated SDK roundtrips for the same entity.

Supported entity types: JOB, PIPELINE, NOTEBOOK, QUERY, DASHBOARD.
"""
from __future__ import annotations

import os
import logging
from functools import lru_cache
from typing import Optional

from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)


def _workspace_url() -> str:
    """Base URL of this workspace (for deep links)."""
    try:
        host = _get_client().config.host.rstrip("/")
        return host
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Per-type resolution
# ---------------------------------------------------------------------------

@lru_cache(maxsize=2048)
def _resolve_job(job_id: str) -> dict:
    """Resolve a Job entity to display name + deep link."""
    base = {"entity_type": "JOB", "entity_id": job_id, "display_name": f"Job {job_id}", "deep_link": None}
    try:
        client = _get_client()
        job = client.jobs.get(job_id=int(job_id))
        base["display_name"] = job.settings.name if job.settings else f"Job {job_id}"
        base["deep_link"] = f"{_workspace_url()}/#job/{job_id}"
        base["creator"] = getattr(job, "creator_user_name", None)
    except Exception as e:
        logger.debug(f"entities: could not resolve job {job_id}: {e}")
    return base


@lru_cache(maxsize=2048)
def _resolve_pipeline(pipeline_id: str) -> dict:
    """Resolve a Pipeline entity to display name + deep link."""
    base = {"entity_type": "PIPELINE", "entity_id": pipeline_id,
            "display_name": f"Pipeline {pipeline_id[:8]}", "deep_link": None}
    try:
        client = _get_client()
        p = client.pipelines.get(pipeline_id=pipeline_id)
        base["display_name"] = p.name or f"Pipeline {pipeline_id[:8]}"
        base["deep_link"] = f"{_workspace_url()}/#joblist/pipelines/{pipeline_id}"
        base["creator"] = getattr(p, "creator_user_name", None)
    except Exception as e:
        logger.debug(f"entities: could not resolve pipeline {pipeline_id}: {e}")
    return base


@lru_cache(maxsize=2048)
def _resolve_notebook(notebook_id: str) -> dict:
    """Resolve a Notebook entity to display name + deep link.

    `notebook_id` may be a numeric workspace ID or a path string.
    """
    base = {"entity_type": "NOTEBOOK", "entity_id": notebook_id,
            "display_name": notebook_id, "deep_link": None}
    try:
        client = _get_client()
        if notebook_id.lstrip("-").isdigit():
            obj = client.workspace.get_status(path=None)  # type: ignore[arg-type]
            # Try by ID — not all SDK versions support direct ID fetch, fall through on error
            obj = client.workspace.get_status(
                path=f"/workspace?object_id={notebook_id}"
            )  # best-effort
            base["display_name"] = getattr(obj, "path", notebook_id).split("/")[-1]
            base["deep_link"] = f"{_workspace_url()}/#notebook/{notebook_id}"
        else:
            # Path-based ID
            base["display_name"] = notebook_id.split("/")[-1]
            base["deep_link"] = f"{_workspace_url()}/#workspace{notebook_id}"
    except Exception as e:
        logger.debug(f"entities: could not resolve notebook {notebook_id}: {e}")
    return base


@lru_cache(maxsize=2048)
def _resolve_query(query_id: str) -> dict:
    """Resolve a SQL Query entity to display name + deep link."""
    base = {"entity_type": "QUERY", "entity_id": query_id,
            "display_name": f"Query {query_id[:8]}", "deep_link": None}
    try:
        client = _get_client()
        q = client.queries.get(id=query_id)
        base["display_name"] = q.name or f"Query {query_id[:8]}"
        base["deep_link"] = f"{_workspace_url()}/sql/queries/{query_id}"
        base["owner"] = getattr(q, "user", {}).get("name") if hasattr(q, "user") else None
    except Exception as e:
        logger.debug(f"entities: could not resolve query {query_id}: {e}")
    return base


@lru_cache(maxsize=2048)
def _resolve_dashboard(dashboard_id: str) -> dict:
    """Resolve a Dashboard entity to display name + deep link."""
    base = {"entity_type": "DASHBOARD", "entity_id": dashboard_id,
            "display_name": f"Dashboard {dashboard_id[:8]}", "deep_link": None}
    try:
        client = _get_client()
        d = client.dashboards.get(dashboard_id=dashboard_id)
        base["display_name"] = d.name or f"Dashboard {dashboard_id[:8]}"
        base["deep_link"] = f"{_workspace_url()}/sql/dashboards/{dashboard_id}"
    except Exception as e:
        logger.debug(f"entities: could not resolve dashboard {dashboard_id}: {e}")
    return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_entity(entity_type: str, entity_id: str) -> dict:
    """Resolve (entity_type, entity_id) to a display dict with deep_link.

    Always returns a dict (never raises).
    """
    et = entity_type.upper()
    if et == "JOB":
        return _resolve_job(entity_id)
    if et == "PIPELINE":
        return _resolve_pipeline(entity_id)
    if et == "NOTEBOOK":
        return _resolve_notebook(entity_id)
    if et == "QUERY":
        return _resolve_query(entity_id)
    if et == "DASHBOARD":
        return _resolve_dashboard(entity_id)
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "display_name": f"{entity_type} {entity_id}",
        "deep_link": None,
    }


def resolve_entities(entities: list[dict]) -> list[dict]:
    """Batch-resolve a list of {entity_type, entity_id} dicts."""
    return [resolve_entity(e.get("entity_type", ""), e.get("entity_id", "")) for e in entities]


def clear_entity_cache() -> None:
    """Evict all in-process entity resolution caches (useful after cache invalidation)."""
    _resolve_job.cache_clear()
    _resolve_pipeline.cache_clear()
    _resolve_notebook.cache_clear()
    _resolve_query.cache_clear()
    _resolve_dashboard.cache_clear()
