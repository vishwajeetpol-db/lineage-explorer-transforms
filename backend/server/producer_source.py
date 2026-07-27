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


# ---------------------------------------------------------------------------
# Unified column-transformation resolver (POC-style precedence)
# ---------------------------------------------------------------------------

def _norm_plan_columns(cols: list[dict]) -> list[dict]:
    """Normalize captured-plan parser output to the panel's column shape."""
    out = []
    for c in cols or []:
        out.append({
            "target_column": c.get("target_column") or c.get("column"),
            "source_columns": c.get("source_columns") or [],
            "expression": c.get("expression") or "",
            "transformation": c.get("notes") or c.get("transformation") or "",
            "category": c.get("category"),
            "confidence": c.get("confidence"),
        })
    return out


def resolve_column_transformations(
    catalog: str,
    schema: str,
    table: str,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    actor: str = "",
    force_rerun: bool = False,
    model: Optional[str] = None,
) -> dict:
    """Resolve a table's per-column transformation lineage using the same
    precedence the reference tool uses, best-source-first:

      1. Captured Spark plan  — exact, deterministic, from the offline
         `lineage_capture` wheel (no LLM).           source='plan_capture'
      2. Captured CDC spec    — apply_changes/AUTO-CDC targets (passthrough).
                                                      source='cdc_spec'
      3. Stored LLM version   — latest saved analysis; stale flag if the
         producer source changed since.              source='stored'
      4. Fresh LLM deduction  — only when nothing above hit (or force_rerun).
                                                      source='llm'

    `force_rerun` skips 1–3 and runs a fresh LLM pass. Returns a superset of the
    analyze_producer shape plus `source` and a `source_label`.
    """
    from backend import plan_capture_service as pcs

    full = f"{catalog}.{schema}.{table}"
    base = {
        "table_full_name": full,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "columns": [],
        "source": None,
        "source_label": None,
        "version": None,
        "versions": [],
        "stale": False,
        "llm_model": None,
        "detail": None,
    }

    # 1 + 2 — offline captured plan / CDC spec (skip when forcing a fresh run).
    if not force_rerun:
        try:
            cap = pcs.get_captured_columns(catalog, schema, table)
        except Exception:
            cap = None
        if cap:
            return {
                **base,
                "columns": _norm_plan_columns(cap["columns"]),
                "source": "plan_capture",
                "source_label": f"Captured Spark plan · v{cap.get('version')} ({cap.get('captured_via') or 'spark'})",
                "version": cap.get("version"),
                "captured_at": cap.get("captured_at"),
            }
        try:
            spec = pcs.get_captured_cdc_spec(catalog, schema, table)
        except Exception:
            spec = None
        if spec:
            keys = spec.get("keys") or []
            return {
                **base,
                "columns": [],  # CDC targets are passthrough from source; no per-column expressions
                "source": "cdc_spec",
                "source_label": f"apply_changes / AUTO CDC spec · v{spec.get('version')} (SCD {spec.get('scd_type')})",
                "cdc_spec": spec,
                "version": spec.get("version"),
                "captured_at": spec.get("captured_at"),
            }

    # 3 + 4 — stored / fresh LLM. When no producer entity is given we can't run
    # the LLM (it needs a producer's source), so return whatever the offline
    # path found plus a hint.
    if not entity_id or not entity_type:
        return {**base, "source": "none",
                "detail": "No captured lineage. Pick a producer entity to run LLM analysis."}

    llm = analyze_producer(
        entity_type=entity_type,
        entity_id=entity_id,
        target_table=full,
        actor=actor,
        force_rerun=force_rerun,
        model=model,
    )
    # analyze_producer already returns source in {stored, llm, unavailable};
    # normalize into this resolver's envelope.
    src = llm.get("source")
    label = {
        "stored": f"Stored LLM analysis · v{llm.get('version')} ({llm.get('llm_model') or 'llm'})",
        "llm": f"Fresh LLM analysis · v{llm.get('version')} ({llm.get('llm_model') or 'llm'})",
        "unavailable": "LLM unavailable",
    }.get(src, src)
    return {
        **base,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "columns": llm.get("columns", []),
        "source": src,
        "source_label": label,
        "version": llm.get("version"),
        "versions": llm.get("versions", []),
        "stale": llm.get("stale", False),
        "llm_model": llm.get("llm_model"),
        "analyzed_at": llm.get("analyzed_at"),
        "detail": llm.get("detail"),
    }


# ---------------------------------------------------------------------------
# Unified cross-source version listing + comparison
# ---------------------------------------------------------------------------

def list_all_versions(
    catalog: str, schema: str, table: str,
    entity_type: Optional[str] = None, entity_id: Optional[str] = None,
) -> list[dict]:
    """Merge captured-plan versions and stored-LLM versions for a table into one
    newest-first list. Each entry carries a `ref` (e.g. 'plan_capture:3' or
    'llm:5') the compare endpoint can resolve back to columns."""
    from backend import plan_capture_service as pcs

    out: list[dict] = []
    try:
        out.extend(pcs.list_captured_versions(catalog, schema, table))
    except Exception:
        pass

    if entity_type and entity_id:
        full = f"{catalog}.{schema}.{table}"
        try:
            for v in analysis_store.list_versions(entity_type, entity_id, full):
                ver = v.get("version")
                out.append({
                    "ref": f"llm:{ver}",
                    "source": "llm",
                    "version": ver,
                    "label": f"LLM v{ver} ({v.get('llm_model') or 'llm'})",
                    "llm_model": v.get("llm_model"),
                    "analyzed_at": str(v.get("analyzed_at")) if v.get("analyzed_at") else None,
                    "analyzed_by": v.get("analyzed_by"),
                })
        except Exception:
            pass
    return out


def _columns_for_ref(
    catalog: str, schema: str, table: str, ref: str,
    entity_type: Optional[str], entity_id: Optional[str],
) -> Optional[dict]:
    """Resolve a version ref ('plan_capture:N' | 'llm:N') to its columns + meta."""
    from backend import plan_capture_service as pcs

    try:
        source, _, vstr = ref.partition(":")
        version = int(vstr)
    except Exception:
        return None

    if source == "plan_capture":
        cap = pcs.get_captured_columns_version(catalog, schema, table, version)
        if not cap:
            return None
        return {
            "ref": ref, "source": "plan_capture", "version": cap.get("version"),
            "label": f"Captured plan v{cap.get('version')}",
            "columns": _norm_plan_columns(cap["columns"]),
            "analyzed_at": cap.get("captured_at"),
        }
    if source == "llm" and entity_type and entity_id:
        full = f"{catalog}.{schema}.{table}"
        row = analysis_store.get_version(entity_type, entity_id, full, version)
        if not row:
            return None
        return {
            "ref": ref, "source": "llm", "version": row.get("version"),
            "label": f"LLM v{row.get('version')}",
            "columns": row.get("columns", []),
            "analyzed_at": row.get("analyzed_at"),
            "source_hash": row.get("source_hash"),
        }
    return None


def compare_transformation_versions(
    catalog: str, schema: str, table: str, ref_from: str, ref_to: str,
    entity_type: Optional[str] = None, entity_id: Optional[str] = None,
) -> dict:
    """Diff two transformation versions from ANY source (captured plan or LLM),
    per-column. Lets you compare, e.g., the exact captured-plan lineage against
    an LLM deduction to see where the model differs from ground truth."""
    import json as _json

    a = _columns_for_ref(catalog, schema, table, ref_from, entity_type, entity_id)
    b = _columns_for_ref(catalog, schema, table, ref_to, entity_type, entity_id)
    if a is None or b is None:
        return {"error": "One or both versions could not be resolved.", "from": a, "to": b}

    def _key(c: dict) -> Optional[str]:
        return c.get("target_column") or c.get("column")

    def _cmp(c: dict) -> str:
        # Compare on the meaningful fields only (expression + source columns),
        # so cross-source cosmetic differences (category naming) don't dominate.
        return _json.dumps({
            "expr": c.get("expression") or c.get("transformation") or "",
            "src": sorted(c.get("source_columns") or []),
        }, sort_keys=True)

    ma = {_key(c): c for c in a["columns"] if _key(c)}
    mb = {_key(c): c for c in b["columns"] if _key(c)}
    diffs = []
    for col in sorted(set(ma) | set(mb)):
        ca, cb = ma.get(col), mb.get(col)
        if ca is None:
            status = "added"
        elif cb is None:
            status = "removed"
        elif _cmp(ca) != _cmp(cb):
            status = "changed"
        else:
            status = "unchanged"
        diffs.append({"column": col, "status": status, "from": ca, "to": cb})

    return {
        "from": {k: a[k] for k in ("ref", "source", "version", "label", "analyzed_at")},
        "to": {k: b[k] for k in ("ref", "source", "version", "label", "analyzed_at")},
        "cross_source": a["source"] != b["source"],
        "column_diffs": diffs,
        "changed_count": sum(1 for d in diffs if d["status"] != "unchanged"),
    }
