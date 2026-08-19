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
# Fetch diagnostics — lets the UI distinguish "producer source is genuinely
# empty" from "the app service principal was denied access to it" (a common,
# user-fixable situation: the pipeline/notebook is owned by someone else).
# ---------------------------------------------------------------------------

# The app's own service-principal client id (injected by the Apps runtime), so
# the "grant access" hint can name exactly which principal needs the grant.
APP_SP_CLIENT_ID = os.environ.get("DATABRICKS_CLIENT_ID", "")


def _is_access_error(exc: Exception) -> bool:
    """True if `exc` looks like an authorization failure (403 / PERMISSION_DENIED)
    rather than a not-found or transient error."""
    name = type(exc).__name__
    if name in ("PermissionDenied", "Unauthorized", "Forbidden"):
        return True
    msg = str(exc).upper()
    return (
        "PERMISSION_DENIED" in msg
        or "PERMISSION DENIED" in msg
        or "DOES NOT HAVE" in msg
        or "NOT AUTHORIZED" in msg
        or "FORBIDDEN" in msg
        or "403" in msg
    )


class _FetchDiag:
    """Accumulates why a producer's source couldn't be read, so the caller can
    surface an actionable message instead of a bare 'no source'."""

    def __init__(self) -> None:
        self.denied_paths: list[str] = []   # workspace paths access was denied to
        self.entity_missing: bool = False   # the producer entity itself is gone

    def note_exception(self, path: str, exc: Exception) -> None:
        if _is_access_error(exc):
            if path and path not in self.denied_paths:
                self.denied_paths.append(path)
        elif "NOT_FOUND" in str(exc).upper() or "was not found" in str(exc):
            self.entity_missing = True

    @property
    def access_denied(self) -> bool:
        return bool(self.denied_paths)


# ---------------------------------------------------------------------------
# Source-code fetchers per entity type
# ---------------------------------------------------------------------------

def _fetch_notebook_source(notebook_id: str, diag: Optional["_FetchDiag"] = None) -> str:
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
        if diag is not None:
            diag.note_exception(notebook_id, e)
        return ""


def _fetch_workspace_file(path: str, diag: Optional["_FetchDiag"] = None) -> str:
    """Read a plain workspace file's text (non-notebook, e.g. a bundle .py/.sql).

    Notebooks are read via `_fetch_notebook_source` (the export API); arbitrary
    workspace files need the download API instead, which returns raw bytes.
    Falls back to the notebook export path if download isn't available.
    """
    client = _get_client()
    try:
        resp = client.workspace.download(path)
        data = resp.read() if hasattr(resp, "read") else resp
        if isinstance(data, bytes):
            return data.decode("utf-8", errors="replace")
        return str(data or "")
    except Exception as e:
        # An access error is meaningful — record it. A plain "not a file, use
        # export" error is not, so only note true auth failures here and fall back.
        if _is_access_error(e) and diag is not None:
            diag.note_exception(path, e)
        logger.info(f"producer_source: download failed for {path}, trying export: {e}")
        return _fetch_notebook_source(path, diag=diag)


def _fetch_query_source(query_id: str, diag: Optional["_FetchDiag"] = None) -> str:
    """Fetch the SQL text of a saved DBSQL query."""
    try:
        client = _get_client()
        q = client.queries.get(id=query_id)
        return q.query or ""
    except Exception as e:
        logger.info(f"producer_source: could not fetch query {query_id}: {e}")
        if diag is not None:
            diag.note_exception(f"query:{query_id}", e)
        return ""


def _fetch_job_source(job_id: str, diag: Optional["_FetchDiag"] = None) -> str:
    """Fetch source code of the first notebook task in a Lakeflow Job."""
    try:
        client = _get_client()
        job = client.jobs.get(job_id=int(job_id))
        tasks = (job.settings.tasks or []) if job.settings else []
        for task in tasks:
            nb = getattr(task, "notebook_task", None)
            if nb and getattr(nb, "notebook_path", None):
                return _fetch_notebook_source(nb.notebook_path, diag=diag)
        # No notebook task found
        return ""
    except Exception as e:
        logger.info(f"producer_source: could not fetch job source for {job_id}: {e}")
        if diag is not None:
            diag.note_exception(f"job:{job_id}", e)
        return ""


# Source-file extensions we try to export when walking a pipeline's glob root.
_PIPELINE_SOURCE_EXTS = (".py", ".sql", ".scala", ".r")


def _list_workspace_source_files(
    root: str, max_files: int = 200, diag: Optional["_FetchDiag"] = None
) -> list[str]:
    """Recursively list source files (notebooks + workspace files) under `root`.

    Used for glob-style pipeline libraries whose source lives as individual
    workspace files rather than declared notebooks. Best-effort: returns [] on
    any error and caps the walk to avoid pathological trees.
    """
    client = _get_client()
    found: list[str] = []
    stack = [root]
    while stack and len(found) < max_files:
        cur = stack.pop()
        try:
            entries = list(client.workspace.list(path=cur))
        except Exception as e:
            logger.info(f"producer_source: could not list workspace dir {cur}: {e}")
            if diag is not None:
                diag.note_exception(cur, e)
            continue
        for obj in entries:
            otype = getattr(obj.object_type, "value", obj.object_type)
            path = obj.path or ""
            if otype == "DIRECTORY":
                stack.append(path)
            elif otype == "NOTEBOOK":
                found.append(path)
            elif otype == "FILE" and path.lower().endswith(_PIPELINE_SOURCE_EXTS):
                found.append(path)
    return found


def _fetch_pipeline_source(pipeline_id: str, diag: Optional["_FetchDiag"] = None) -> str:
    """Concatenate source from all libraries in a pipeline.

    Handles the three library shapes a Lakeflow/DLT pipeline can declare:
      * ``notebook.path`` — the classic single-notebook library.
      * ``file.path``     — a direct workspace-file source reference.
      * ``glob.include``  — modern bundle-deployed pipelines whose source is a
        directory of ``.py``/``.sql`` files matched by a glob. We walk the
        glob's base directory and export every source file under it.
    Previously only ``notebook`` was handled, so glob/file pipelines yielded no
    source — surfacing to the UI as "No source code available" (mislabeled as
    "LLM unavailable").
    """
    try:
        client = _get_client()
        # Read the RAW pipeline spec via the REST API rather than the typed SDK
        # object: the `glob` library field is newer than some SDK versions in our
        # supported range, and an older SDK silently drops unrecognized fields on
        # deserialization — which made glob/file pipelines look library-less.
        try:
            raw = client.api_client.do("GET", f"/api/2.0/pipelines/{pipeline_id}")
        except Exception as e:
            if diag is not None:
                diag.note_exception(f"pipeline:{pipeline_id}", e)
            raw = {}
        libraries = ((raw.get("spec") or {}).get("libraries")) or []
        parts: list[str] = []
        seen: set[str] = set()

        def _add(path: str, label: str, is_notebook: bool) -> None:
            if not path or path in seen:
                return
            seen.add(path)
            src = (_fetch_notebook_source(path, diag=diag) if is_notebook
                   else _fetch_workspace_file(path, diag=diag))
            if src:
                parts.append(f"# --- {label}: {path} ---\n{src}")

        for lib in libraries:
            nb = (lib.get("notebook") or {}).get("path")
            if nb:
                _add(nb, "Notebook", True)
                continue

            fl = (lib.get("file") or {}).get("path")
            if fl:
                _add(fl, "File", False)
                continue

            include = (lib.get("glob") or {}).get("include")
            if include:
                # Strip trailing glob wildcards to get a base directory to walk,
                # e.g. ".../transformations/**" -> ".../transformations".
                base = include.split("*", 1)[0].rstrip("/")
                for path in _list_workspace_source_files(base, diag=diag):
                    # export handles both, but plain files download more reliably;
                    # treat .py/.sql/etc. as files, everything else as a notebook.
                    is_nb = not path.lower().endswith(_PIPELINE_SOURCE_EXTS)
                    _add(path, "File", is_nb)

        if not parts:
            logger.info(
                f"producer_source: pipeline {pipeline_id} yielded no readable source "
                f"({len(libraries)} librar(ies) inspected)"
            )
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


def _fetch_source(entity_type: str, entity_id: str, diag: Optional["_FetchDiag"] = None) -> str:
    """Dispatch source-code fetch to the right fetcher."""
    et = entity_type.upper()
    if et == "NOTEBOOK":
        return _fetch_notebook_source(entity_id, diag=diag)
    if et == "QUERY":
        return _fetch_query_source(entity_id, diag=diag)
    if et == "JOB":
        return _fetch_job_source(entity_id, diag=diag)
    if et == "PIPELINE":
        return _fetch_pipeline_source(entity_id, diag=diag)
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
        result["reason_code"] = "llm_not_configured"
        result["detail"] = "LLM not configured — no reachable serving endpoint for this app."
        return result

    # ---- Fresh analysis path ----
    diag = _FetchDiag()
    source_code = _fetch_source(entity_type, entity_id, diag=diag)
    if not source_code.strip():
        if diag.access_denied:
            # The producer's code exists but the app service principal can't read
            # it — a common, user-fixable situation. Return an actionable reason
            # + the exact resource(s) to grant so the UI can guide the fix.
            result["reason_code"] = "access_denied"
            result["denied_paths"] = diag.denied_paths
            result["app_service_principal"] = APP_SP_CLIENT_ID or None
            paths_str = ", ".join(diag.denied_paths[:5])
            sp_str = (f" to the app's service principal ({APP_SP_CLIENT_ID})"
                      if APP_SP_CLIENT_ID else " to the app's service principal")
            result["detail"] = (
                f"The app can't read this {entity_type.lower()}'s source code — access was "
                f"denied to: {paths_str}. Grant CAN_READ (or CAN_VIEW){sp_str} on the "
                f"producing {entity_type.lower()} and its source files, then re-analyze."
            )
        elif diag.entity_missing:
            result["reason_code"] = "entity_missing"
            result["detail"] = (
                f"The producing {entity_type.lower()} ({entity_id}) no longer exists, "
                f"so its source can't be read."
            )
        else:
            result["reason_code"] = "no_source"
            result["detail"] = (
                "No source code available for this entity — the producer may be a "
                "SQL/DLT/external writer whose source can't be fetched."
            )
        return result

    result["source_hash"] = analysis_store._source_hash(source_code)

    # Always give the LLM the full target column list so it accounts for EVERY
    # output column (pass-through ones included).
    if not target_columns:
        target_columns = _fetch_target_columns(target_table)

    used_model = model or llm_client.LLM_MODEL_NAME
    try:
        analysis = llm_client.analyze_source_code(
            source_code=source_code,
            target_table=target_table,
            target_columns=target_columns,
            model=used_model,
        )
    except Exception as e:
        # A real LLM failure (endpoint error, bad request for the model, timeout,
        # unparseable output) — surface the specific message, not a generic label.
        result["reason_code"] = "llm_error"
        result["llm_model"] = used_model
        result["detail"] = f"LLM analysis failed on {used_model}: {str(e)[:400]}"
        return result
    # If NOTHING in the analysis carries real lineage (every column came back
    # UNKNOWN/NULL), the source is a metadata-driven framework — signal the deep
    # config-based fallback instead of saving a useless all-UNKNOWN version.
    # NOTE: only the all-or-nothing case is rejected. When at least one column is
    # meaningful we keep the FULL analysis, including complex/struct columns a
    # model may label UNKNOWN (e.g. `_lineage`, `_dq`) — dropping those made valid
    # columns disappear for some models.
    if not any(_is_meaningful_column(c) for c in (analysis or [])):
        result["reason_code"] = "no_columns"
        result["detail"] = (
            "The source was analysed but no concrete column logic was found (results "
            "came back UNKNOWN) — this looks like a metadata-driven framework. Run deep "
            "framework analysis to derive columns from its config tables and parameters."
        )
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

def _is_meaningful_column(c: dict) -> bool:
    """True only if a column analysis carries REAL lineage — not an UNKNOWN/NULL
    placeholder. Models handed generic (metadata-driven) framework code often
    return an entry per output column with category UNKNOWN and no source/expr;
    those must NOT count as a valid transformation (they'd otherwise mask the
    deep config-based fallback)."""
    cat = (c.get("category") or "").strip().upper()
    if cat == "UNKNOWN":
        return False
    expr = (c.get("expression") or c.get("transformation") or "").strip().upper()
    srcs = c.get("source_columns") or []
    if not srcs and expr in ("", "UNKNOWN", "NULL", "NONE", "N/A", "?", "-"):
        return False
    return True


def _producer_display(entity_type: Optional[str], entity_id: Optional[str]) -> str:
    """A plain fallback label for a producer (e.g. 'PIPELINE 84ad1944…'). Cheap —
    no API call; the frontend enriches this with the graph's display name when it
    has one, so panel-open latency isn't spent resolving a friendly name here."""
    et = (entity_type or "").upper()
    eid = entity_id or ""
    short = f"{eid[:8]}…" if len(eid) > 10 else eid
    return f"{et} {short}".strip() if et or short else ""


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
        "reason_code": None,
        "denied_paths": None,
        "app_service_principal": None,
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
    # the LLM (it needs a producer's source) — but a prior LLM analysis may
    # already be stored for one of this table's producers. Surface the most
    # recent one (labelled with the producer it came from) instead of a
    # misleading "no lineage yet": opening the panel and then picking that same
    # producer on the Analyze tab must not contradict each other.
    if not entity_id or not entity_type:
        if not force_rerun:
            prior = analysis_store.get_latest_for_table(full)
            if prior and prior.get("columns"):
                pet, peid = prior.get("entity_type"), prior.get("entity_id")
                cur_hash = _current_source_hash(pet, peid) if pet and peid else None
                return {
                    **base,
                    "entity_type": pet,
                    "entity_id": peid,
                    "columns": prior["columns"],
                    "source": "stored",
                    "source_label": (
                        f"Stored LLM analysis · v{prior.get('version')} "
                        f"({prior.get('llm_model') or 'llm'})"
                    ),
                    "producer_label": _producer_display(pet, peid),
                    "version": prior.get("version"),
                    "versions": analysis_store.list_versions(pet, peid, full),
                    "llm_model": prior.get("llm_model"),
                    "analyzed_at": prior.get("analyzed_at"),
                    "stale": bool(
                        cur_hash and prior.get("source_hash")
                        and cur_hash != prior["source_hash"]
                    ),
                }
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
        # Actionable failure metadata (present when source couldn't be read):
        "reason_code": llm.get("reason_code"),
        "denied_paths": llm.get("denied_paths"),
        "app_service_principal": llm.get("app_service_principal"),
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


def overview_column_transformations(
    catalog: str, schema: str, table: str,
    entity_type: Optional[str] = None, entity_id: Optional[str] = None,
    actor: str = "", refresh: bool = False, model: Optional[str] = None,
) -> dict:
    """Plain-English LLM overview of a table's column transformations.

    Resolves the columns best-source-first (captured plan / CDC / stored / LLM),
    then asks the LLM for an overall `summary` plus a per-column `explanation`,
    merged back onto each column (which keeps its source_columns/expression/
    category so the UI can draw the source→transform→target graphic). Cached per
    table in the shared capability cache; `refresh` re-runs the LLM."""
    from backend.server import capability_cache as cc

    full = f"{catalog}.{schema}.{table}"

    def _compute() -> dict:
        resolved = resolve_column_transformations(
            catalog, schema, table, entity_type, entity_id, actor=actor,
        )
        cols = resolved.get("columns") or []
        overview = llm_client.explain_transformations(cols, full, model=model)
        expl = {e.get("column"): e.get("explanation", "") for e in (overview.get("columns") or [])}
        out_cols = [
            {**c, "explanation": expl.get(c.get("target_column") or c.get("column"), "")}
            for c in cols
        ]
        return {
            "table_full_name": full,
            "source": resolved.get("source"),
            "source_label": resolved.get("source_label"),
            "version": resolved.get("version"),
            "summary": overview.get("summary", ""),
            "columns": out_cols,
            "error": overview.get("error"),
        }

    return cc.serve_or_compute(full, "ct_overview", _compute, actor=actor, refresh=refresh)


def compare_transformation_versions(
    catalog: str, schema: str, table: str, ref_from: str, ref_to: str,
    entity_type: Optional[str] = None, entity_id: Optional[str] = None,
) -> dict:
    """Diff two transformation versions from ANY source (captured plan or LLM),
    per-column. Lets you compare, e.g., the exact captured-plan lineage against
    an LLM deduction to see where the model differs from ground truth."""
    a = _columns_for_ref(catalog, schema, table, ref_from, entity_type, entity_id)
    b = _columns_for_ref(catalog, schema, table, ref_to, entity_type, entity_id)
    if a is None or b is None:
        return {"error": "One or both versions could not be resolved.", "from": a, "to": b}

    def _key(c: dict) -> Optional[str]:
        return c.get("target_column") or c.get("column")

    def _cmp(c: dict) -> str:
        # Compare on the normalized expression only — that's the authoritative
        # "how the column is computed" and exactly what the diff UI renders.
        # `source_columns` is DERIVED from the expression and, for captured Spark
        # plans, the parser can emit cosmetically different source-column sets for
        # two captures of the SAME plan (identical expression). Keying on it made
        # every column show as "changed" against an identical expression — a false
        # diff. Whitespace is stripped so re-captures don't diff on formatting.
        return (c.get("expression") or c.get("transformation") or "").strip()

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


def _cmp_key(c: dict) -> str:
    """Canonical form of a column's transformation for divergence detection —
    expression + source columns only (ignores cosmetic category naming)."""
    import json as _json
    return _json.dumps({
        "expr": (c.get("expression") or c.get("transformation") or "").strip(),
        "src": sorted(c.get("source_columns") or []),
    }, sort_keys=True)


def compare_producers(
    catalog: str, schema: str, table: str, producers: list[dict],
    actor: str = "", force_rerun: bool = False,
) -> dict:
    """Resolve column transformations for MULTIPLE producers of the same table
    and build a per-column comparison matrix.

    A table can be written by more than one job/pipeline (e.g. two jobs that both
    populate `orders_curated`). Each may compute the same output column with
    DIFFERENT logic — a real consistency hazard. This resolves each producer via
    the normal best-source-first precedence (cached; only calls the LLM where
    nothing is stored) and returns:

      producers[]:  one entry per producer (label, source, resolve status)
      columns[]:    one row per target column, with a per-producer cell
                    (expression / source_columns / category) and a `divergent`
                    flag set when producers that DO define the column disagree.

    `producers` is a list of {"entity_type", "entity_id"} dicts.
    """
    full = f"{catalog}.{schema}.{table}"
    resolved: list[dict] = []
    for p in producers:
        et = (p.get("entity_type") or "").strip().upper()
        eid = (p.get("entity_id") or "").strip()
        if not et or not eid:
            continue
        # Resolve each producer from ITS OWN source (stored-or-fresh LLM keyed by
        # entity), NOT the table-level precedence: a captured Spark plan / CDC spec
        # is keyed by table, so it would return the same result for every producer
        # and hide exactly the divergence this comparison exists to surface.
        try:
            r = analyze_producer(
                entity_type=et, entity_id=eid, target_table=full,
                actor=actor, force_rerun=force_rerun,
            )
            # analyze_producer returns source in {stored, llm, unavailable}; give the
            # matrix a friendly per-producer label.
            src = r.get("source")
            r["source_label"] = {
                "stored": f"Stored LLM v{r.get('version')} ({r.get('llm_model') or 'llm'})",
                "llm": f"Fresh LLM v{r.get('version')} ({r.get('llm_model') or 'llm'})",
                "unavailable": "Source unavailable",
            }.get(src, src)
        except Exception as e:
            r = {"source": "unavailable", "columns": [], "detail": str(e), "source_label": "Source unavailable"}
        # Stable per-producer key for matrix cells.
        pkey = f"{et}:{eid}"
        resolved.append({
            "key": pkey,
            "entity_type": et,
            "entity_id": eid,
            "label": r.get("source_label") or f"{et} {eid[:8]}",
            "source": r.get("source"),
            "reason_code": r.get("reason_code"),
            "detail": r.get("detail"),
            "columns": {(c.get("target_column") or c.get("column")): c
                        for c in (r.get("columns") or []) if (c.get("target_column") or c.get("column"))},
        })

    # Union of all target columns across producers, preserving first-seen order.
    col_order: list[str] = []
    seen: set[str] = set()
    for rp in resolved:
        for col in rp["columns"]:
            if col not in seen:
                seen.add(col)
                col_order.append(col)

    rows: list[dict] = []
    divergent_count = 0
    for col in col_order:
        cells = []
        present_keys = []
        for rp in resolved:
            c = rp["columns"].get(col)
            if c is not None:
                present_keys.append(_cmp_key(c))
            cells.append({
                "producer": rp["key"],
                "present": c is not None,
                "expression": (c.get("expression") or c.get("transformation")) if c else None,
                "source_columns": (c.get("source_columns") or []) if c else [],
                "category": c.get("category") if c else None,
            })
        # Divergent = at least two producers define it AND they don't all agree,
        # OR some producers define it and others (that produce this table) omit it.
        defining = sum(1 for cell in cells if cell["present"])
        divergent = (len(set(present_keys)) > 1) or (0 < defining < len(resolved))
        if divergent:
            divergent_count += 1
        rows.append({"column": col, "divergent": divergent, "cells": cells})

    return {
        "table_full_name": f"{catalog}.{schema}.{table}",
        "producers": [{k: rp[k] for k in ("key", "entity_type", "entity_id", "label", "source", "reason_code", "detail")}
                      for rp in resolved],
        "columns": rows,
        "divergent_count": divergent_count,
        "column_count": len(rows),
    }
