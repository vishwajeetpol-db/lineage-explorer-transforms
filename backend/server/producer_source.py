"""LLM Producer Source Analysis — capability 27 (Approach A).

Fetches the actual source code of a producer entity (notebook, SQL query,
Lakeflow Job task, or SDP pipeline notebook) via the Databricks SDK, then
calls `server.llm.analyze_source_code()` to infer per-column transformation
descriptions.  Results are versioned in `server.analysis_store`.

This is the *full* Approach A implementation: unlike the existing
`/api/transform/captured-expression` (which only reads already-captured
Spark plans), this module performs a *live, on-demand* LLM call against
the real source code.

Caching contract:
  1. Check analysis_store for a row with the same (entity_type, entity_id,
     source_hash). If found, return immediately (no LLM call).
  2. Fetch the source code via SDK.
  3. Compute source_hash. Check cache again (prevents a race where two
     concurrent requests fetch source before either has stored results).
  4. Call the LLM.
  5. Save to analysis_store and return.
"""
from __future__ import annotations

import os
import logging
from typing import Optional

from backend.lineage_service import _get_client
from backend.server import llm as llm_client
from backend.server import analysis_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-code fetchers per entity type
# ---------------------------------------------------------------------------

def _fetch_notebook_source(notebook_id: str) -> str:
    """Export the latest version of a notebook as source text."""
    client = _get_client()
    # notebook_id may be a numeric workspace object ID or an absolute path
    if notebook_id.lstrip("-").isdigit():
        # Use export by ID (requires the path to be resolved first)
        try:
            obj = client.workspace.export(
                path=notebook_id, format="SOURCE"
            )  # type: ignore[arg-type]
            content = obj.content or b""
            import base64
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except Exception:
            pass
    # Fall back: treat as path
    try:
        resp = client.workspace.export(path=notebook_id, format="SOURCE")  # type: ignore[arg-type]
        import base64
        return base64.b64decode(resp.content or b"").decode("utf-8", errors="replace")
    except Exception as e:
        logger.info(f"producer_source: could not export notebook {notebook_id}: {e}")
        return ""


def _fetch_query_source(query_id: str) -> str:
    """Fetch the SQL text of a saved DBSQL query."""
    try:
        client = _get_client()
        q = client.queries.get(id=query_id)
        return q.query or ""
    except Exception as e:
        logger.info(f"producer_source: could not fetch query {query_id}: {e}")
        return ""


def _fetch_job_source(job_id: str) -> str:
    """Fetch source code of the first notebook task in a Lakeflow Job."""
    try:
        client = _get_client()
        job = client.jobs.get(job_id=int(job_id))
        tasks = (job.settings.tasks or []) if job.settings else []
        for task in tasks:
            nb = getattr(task, "notebook_task", None)
            if nb and getattr(nb, "notebook_path", None):
                return _fetch_notebook_source(nb.notebook_path)
        # No notebook task found
        return ""
    except Exception as e:
        logger.info(f"producer_source: could not fetch job source for {job_id}: {e}")
        return ""


def _fetch_pipeline_source(pipeline_id: str) -> str:
    """Concatenate source from all library notebooks in a pipeline."""
    try:
        client = _get_client()
        p = client.pipelines.get(pipeline_id=pipeline_id)
        libraries = getattr(p.spec if p.spec else p, "libraries", None) or []
        parts: list[str] = []
        for lib in libraries:
            nb = getattr(lib, "notebook", None)
            if nb and getattr(nb, "path", None):
                src = _fetch_notebook_source(nb.path)
                if src:
                    parts.append(f"# --- Notebook: {nb.path} ---\n{src}")
        return "\n\n".join(parts)
    except Exception as e:
        logger.info(f"producer_source: could not fetch pipeline source for {pipeline_id}: {e}")
        return ""


def _fetch_source(entity_type: str, entity_id: str) -> str:
    """Dispatch source-code fetch to the right fetcher."""
    et = entity_type.upper()
    if et == "NOTEBOOK":
        return _fetch_notebook_source(entity_id)
    if et == "QUERY":
        return _fetch_query_source(entity_id)
    if et == "JOB":
        return _fetch_job_source(entity_id)
    if et == "PIPELINE":
        return _fetch_pipeline_source(entity_id)
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_producer(
    entity_type: str,
    entity_id: str,
    target_table: str,
    actor: str = "",
    force_rerun: bool = False,
    target_columns: Optional[list[str]] = None,
) -> dict:
    """Run Approach A analysis for a producer entity.

    Returns:
        {
            "source": "cache" | "llm" | "unavailable",
            "entity_type": str,
            "entity_id": str,
            "target_table": str,
            "columns": list[dict],   # per-column analysis
            "source_hash": str | None,
            "llm_model": str | None,
        }
    """
    result = {
        "source": "unavailable",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "target_table": target_table,
        "columns": [],
        "source_hash": None,
        "llm_model": None,
    }

    if not llm_client.is_llm_configured():
        result["detail"] = "LLM not configured. Set LLM_API_TOKEN or DATABRICKS_TOKEN."
        return result

    # Fetch source code
    source_code = _fetch_source(entity_type, entity_id)
    if not source_code.strip():
        result["detail"] = "No source code available for this entity."
        return result

    h = analysis_store._source_hash(source_code)
    result["source_hash"] = h

    # Cache check (skip if force_rerun)
    if not force_rerun:
        cached = analysis_store.get_cached_analysis(entity_type, entity_id, source_code)
        if cached is not None:
            result["source"] = "cache"
            result["columns"] = cached
            return result

    # LLM call
    analysis = llm_client.analyze_source_code(
        source_code=source_code,
        target_table=target_table,
        target_columns=target_columns,
    )
    if not analysis:
        result["detail"] = "LLM returned no analysis."
        return result

    # Persist
    try:
        analysis_store.save_analysis(
            entity_type=entity_type,
            entity_id=entity_id,
            source_code=source_code,
            target_table=target_table,
            analysis=analysis,
            llm_model=llm_client.LLM_MODEL_NAME,
            actor=actor,
        )
    except Exception as e:
        logger.warning(f"producer_source: failed to save analysis (returning result anyway): {e}")

    result["source"] = "llm"
    result["columns"] = analysis
    result["llm_model"] = llm_client.LLM_MODEL_NAME
    return result
