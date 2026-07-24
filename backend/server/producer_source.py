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

from databricks.sdk.service.workspace import ExportFormat

from backend.lineage_service import _get_client
from backend.server import llm as llm_client
from backend.server import analysis_store

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Source-code fetchers per entity type
# ---------------------------------------------------------------------------

def _fetch_notebook_source(notebook_id: str) -> str:
    """Export the latest version of a notebook as source text.

    `format` must be an ExportFormat enum — passing the bare string "SOURCE"
    makes the SDK raise `'str' object has no attribute 'value'`.
    """
    import base64
    client = _get_client()
    try:
        resp = client.workspace.export(path=notebook_id, format=ExportFormat.SOURCE)
        content = resp.content or ""
        # The export API returns base64-encoded content.
        return base64.b64decode(content).decode("utf-8", errors="replace")
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


def _fetch_target_columns(target_table: str) -> list[str]:
    """Return the target table's column names from information_schema, so the LLM
    can be told to account for EVERY output column (not just the interesting ones)."""
    parts = target_table.split(".")
    if len(parts) != 3:
        return []
    catalog, schema, table = parts
    try:
        from backend.server.governance import get_table_governance
        gov = get_table_governance(catalog, schema, table)
        return [c["name"] for c in gov.get("columns", []) if c.get("name")]
    except Exception as e:
        logger.info(f"producer_source: could not fetch target columns for {target_table}: {e}")
        return []


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

def _current_source_hash(entity_type: str, entity_id: str) -> Optional[str]:
    """Hash the producer's CURRENT source, for stale-detection. None if unreadable."""
    try:
        src = _fetch_source(entity_type, entity_id)
        return analysis_store._source_hash(src) if src.strip() else None
    except Exception:
        return None


def analyze_producer(
    entity_type: str,
    entity_id: str,
    target_table: str,
    actor: str = "",
    force_rerun: bool = False,
    target_columns: Optional[list[str]] = None,
    model: Optional[str] = None,
) -> dict:
    """Run (or load) Approach A analysis for a producer entity.

    Behaviour:
      * force_rerun=False (default): return the LATEST stored version if one
        exists, and set `stale=True` when the producer's current source hash
        differs from that version's — which the UI uses to enable "Re-analyze".
        Only runs the LLM if nothing has ever been stored.
      * force_rerun=True: fetch source, run the LLM (optionally with a specific
        `model`), and append a new version.

    Returns a dict with source/columns/version/versions/stale/llm_model etc.
    """
    result = {
        "source": "unavailable",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "target_table": target_table,
        "columns": [],
        "source_hash": None,
        "llm_model": None,
        "version": None,
        "versions": [],
        "stale": False,
    }

    # ---- Load path: return the latest stored version unless forced ----
    if not force_rerun:
        latest = analysis_store.get_latest_version(entity_type, entity_id, target_table)
        if latest is not None:
            cur_hash = _current_source_hash(entity_type, entity_id)
            result.update({
                "source": "stored",
                "columns": latest["columns"],
                "source_hash": latest["source_hash"],
                "llm_model": latest["llm_model"],
                "version": latest["version"],
                "analyzed_at": latest["analyzed_at"],
                "analyzed_by": latest["analyzed_by"],
                "versions": analysis_store.list_versions(entity_type, entity_id, target_table),
                # Stale when we can read current source AND it differs from stored.
                "stale": bool(cur_hash and latest["source_hash"] and cur_hash != latest["source_hash"]),
            })
            return result

    if not llm_client.is_llm_configured():
        result["detail"] = "LLM not configured — no reachable serving endpoint."
        return result

    # ---- Fresh analysis path ----
    source_code = _fetch_source(entity_type, entity_id)
    if not source_code.strip():
        result["detail"] = "No source code available for this entity."
        return result

    result["source_hash"] = analysis_store._source_hash(source_code)

    # Always give the LLM the full target column list so it accounts for EVERY
    # output column (pass-through ones included).
    if not target_columns:
        target_columns = _fetch_target_columns(target_table)

    used_model = model or llm_client.LLM_MODEL_NAME
    analysis = llm_client.analyze_source_code(
        source_code=source_code,
        target_table=target_table,
        target_columns=target_columns,
        model=used_model,
    )
    if not analysis:
        result["detail"] = "LLM returned no analysis."
        return result

    new_version = 1
    try:
        new_version = analysis_store.save_analysis(
            entity_type=entity_type,
            entity_id=entity_id,
            source_code=source_code,
            target_table=target_table,
            analysis=analysis,
            llm_model=used_model,
            actor=actor,
        )
    except Exception as e:
        logger.warning(f"producer_source: failed to save analysis (returning result anyway): {e}")

    result["source"] = "llm"
    result["columns"] = analysis
    result["llm_model"] = used_model
    result["version"] = new_version
    result["stale"] = False
    result["versions"] = analysis_store.list_versions(entity_type, entity_id, target_table)
    return result
