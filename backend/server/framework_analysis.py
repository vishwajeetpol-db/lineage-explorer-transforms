"""Deep fallback analysis for metadata-driven / configurable ETL frameworks.

When the normal producer-source analysis returns no columns, the producer is
usually a *generic* framework engine whose per-column mappings live in CONFIG
TABLES and/or PARAMETERS rather than in the code. This module runs a second,
agentic pass that:

  1. detects the config mechanism from the source (LLM),
  2. reads the pipeline/job parameters,
  3. queries the identified config table(s),
  4. derives per-column transformations from that config (LLM),

emitting a step-by-step commentary as it goes. `deep_analyze_stream` is a
generator of event dicts so the route can stream them as NDJSON.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Iterator, Optional

from backend.server import llm as llm_client
from backend.server import analysis_store
from backend.server.producer_source import _fetch_source, _fetch_target_columns, _is_meaningful_column

logger = logging.getLogger(__name__)

_FQN_RE = re.compile(r"^[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/=]+$")
_MAX_CONFIG_ROWS = 100
# Well-known keys a config row/object might use to name the TARGET it applies to.
# A framework's own key column is whatever it chose, so these are only the
# fallback — see _target_keys(), which unions them with the detected columns.
_TARGET_KEYS = ("target_name", "target", "name", "output_table", "target_table", "entity", "entity_name", "dataset")
# Human-readable producer nouns for user-facing prose. Raw entity types are
# upper-snake identifiers (MATERIALIZED_VIEW) and must never reach the UI text.
_ENTITY_LABELS = {
    "JOB": "job", "PIPELINE": "pipeline", "NOTEBOOK": "notebook", "VIEW": "view",
    "MATERIALIZED_VIEW": "materialized view", "STREAMING_TABLE": "streaming table",
    "SQL_TASK": "SQL task", "DLT_PIPELINE": "pipeline",
}


def _ev(step: str, status: str, message: str, **extra) -> dict:
    """Build a commentary event."""
    return {"type": "step", "step": step, "status": status, "message": message, **extra}


def _norm_name(s: object) -> str:
    """Normalize a table/target name for matching: lower, drop backticks, strip a
    trailing `_target`/`_dq`, keep only the last dotted part."""
    t = str(s).lower().strip().strip("`").split(".")[-1]
    for suf in ("_target", "_dq"):
        if t.endswith(suf):
            t = t[: -len(suf)]
    return t


def _entity_label(entity_type: str) -> str:
    """Human-readable producer noun for user-facing prose — never the raw
    upper-snake identifier (`MATERIALIZED_VIEW` → "materialized view"). An unknown
    or missing type degrades to the generic "producer" rather than an empty word."""
    et = (entity_type or "").strip().upper()
    if not et:
        return "producer"
    return _ENTITY_LABELS.get(et, et.lower().replace("_", " "))


def _target_keys(detected: object) -> tuple[str, ...]:
    """The framework's OWN detected `target_key_columns` unioned with the
    well-known defaults, de-duplicated and order-preserving (detected first).

    Without the detected keys, a config table keyed by e.g. `tgt_tbl` matches
    nothing, every row falls back to a blind sample, and derivation reports "no
    columns" for a config that was sitting right there."""
    out: list[str] = []
    for k in list(detected or []) + list(_TARGET_KEYS):
        if isinstance(k, str) and k.strip() and k.strip() not in out:
            out.append(k.strip())
    return tuple(out)


def _blockers_note(empty: list[str], unreadable: list[str], uncertain: list[str]) -> str:
    """Sentence(s) naming the config tables that couldn't contribute, so a failure
    never blames the wrong cause: an unreadable table is a grant problem, an empty
    one is a run-the-pipeline problem, and a guessed name may be the wrong table
    entirely. Each needs different remediation, so each is stated explicitly."""
    parts: list[str] = []
    if unreadable:
        parts.append(f"Config table(s) {', '.join(unreadable)} could not be read — grant the app's "
                     f"service principal SELECT on them.")
    if empty:
        parts.append(f"Config table(s) {', '.join(empty)} are currently empty.")
    if uncertain:
        parts.append(f"The name(s) {', '.join(uncertain)} were inferred from the source rather than read "
                     f"directly, so the real config table may be a different one.")
    return " ".join(parts)


def _maybe_decode(v: object) -> object:
    """Unwrap a config cell that stores structured config as a string — base64-of-
    JSON (common, to survive SQL round-trips) or plain JSON — so the LLM sees the
    real structure instead of an opaque blob. Non-structured strings pass through."""
    if not isinstance(v, str) or len(v) < 2:
        return v
    s = v.strip()
    if len(s) >= 16 and len(s) % 4 == 0 and _B64_RE.match(s):
        try:
            return json.loads(base64.b64decode(s).decode("utf-8"))
        except Exception:
            pass
    if s[:1] in "{[":
        try:
            return json.loads(s)
        except Exception:
            pass
    return v


def _focus_on_target(obj: object, target: str, keys: tuple[str, ...] = _TARGET_KEYS) -> tuple[object, bool]:
    """Recursively prune lists of target-describing dicts down to the entry(ies)
    matching `target` (normalized). Returns (pruned, matched). Lets a multi-target
    config collapse to just the relevant target's columns.

    `keys` are the column names that may name the target — pass the framework's
    detected keys (via _target_keys) so a non-standard key column still matches."""
    matched = False
    if isinstance(obj, dict):
        out = {}
        for k, val in obj.items():
            pv, m = _focus_on_target(val, target, keys)
            out[k] = pv
            matched = matched or m
        return out, matched
    if isinstance(obj, list):
        keyed = [x for x in obj if isinstance(x, dict) and any(k in x for k in keys)]
        use_list = obj
        if keyed:
            keep = [x for x in keyed
                    if any(_norm_name(x[k]) == target for k in keys if k in x)]
            if keep:
                use_list, matched = keep, True
        out = []
        for x in use_list:
            pv, m = _focus_on_target(x, target, keys)
            out.append(pv)
            matched = matched or m
        return out, matched
    return obj, matched


def _fetch_entity_parameters(entity_type: str, entity_id: str) -> dict:
    """Best-effort read of a producer's parameters (pipeline configuration or job
    parameters / notebook base_parameters). Returns {} on any failure."""
    from backend.lineage_service import _get_client
    et = (entity_type or "").upper()
    try:
        client = _get_client()
        if client is None:
            return {}
        if et == "PIPELINE":
            raw = client.api_client.do("GET", f"/api/2.0/pipelines/{entity_id}")
            return dict(((raw.get("spec") or {}).get("configuration")) or {})
        if et == "JOB":
            raw = client.api_client.do("GET", f"/api/2.1/jobs/get?job_id={entity_id}")
            settings = raw.get("settings") or {}
            params: dict = {}
            for p in settings.get("parameters") or []:
                if p.get("name") is not None:
                    params[p["name"]] = p.get("default")
            for task in settings.get("tasks") or []:
                nb = (task.get("notebook_task") or {}).get("base_parameters") or {}
                params.update(nb)
            return params
    except Exception as e:
        logger.info(f"framework_analysis: could not read parameters for {et} {entity_id}: {e}")
    return {}


def _query_config_table(fqn: str, target_table: str, key_columns: list[str]) -> Optional[dict]:
    """SELECT a config table (validated, row-capped) and return the rows most
    relevant to the target table. Filters in Python (no dynamic WHERE) by matching
    the target table's short name against `key_columns` (the framework's own
    detected target-key columns) unioned with the well-known defaults. Returns None
    if the table can't be read."""
    if not _FQN_RE.match(fqn or ""):
        return None
    from backend.lineage_service import _get_client, _execute_sql
    client = _get_client()
    if client is None:
        return None
    rows = _execute_sql(client, f"SELECT * FROM {fqn} LIMIT 200")
    if not rows:
        return {"table": fqn, "columns": [], "rows": [], "total_rows": 0, "matched": False}
    columns = list(rows[0].keys())
    target_norm = _norm_name(target_table)
    keys = _target_keys(key_columns)

    # Decode any structured (base64/JSON) config cells, then focus each row on the
    # target — so a multi-target config collapses to just the relevant columns and
    # the LLM never sees an opaque blob.
    focused_rows: list[tuple[dict, bool]] = []
    for r in rows:
        decoded = {k: _maybe_decode(v) for k, v in r.items()}
        pruned, m = _focus_on_target(decoded, target_norm, keys)
        focused_rows.append((pruned, m))
    any_match = any(m for _, m in focused_rows)

    if any_match:
        use = [d for d, m in focused_rows if m][:_MAX_CONFIG_ROWS]
    else:
        # No row explicitly names this target — fall back to a small decoded sample.
        use = [d for d, _ in focused_rows][:10]
    return {
        "table": fqn,
        "columns": columns,
        "rows": use,
        "total_rows": len(rows),
        "matched": any_match,
    }


def deep_analyze_stream(
    entity_type: str, entity_id: str, target_table: str,
    actor: str = "", model: Optional[str] = None,
) -> Iterator[dict]:
    """Run the framework fallback, yielding commentary events then a final
    `result` event with derived columns (and the saved version)."""
    et = (entity_type or "").upper()
    yield _ev("start", "running", "Primary analysis found no columns — this looks like a metadata-driven framework. Starting deep analysis.")

    # 1. Source
    source_code = _fetch_source(et, entity_id)
    if not source_code.strip():
        yield _ev("fetch_source", "error", "Could not read the producer's source code, so config detection isn't possible.")
        yield {"type": "result", "columns": [], "derived": False, "reason_code": "no_source",
               "detail": "No source code to analyse."}
        return
    yield _ev("fetch_source", "ok", f"Loaded framework source ({len(source_code):,} chars).")

    # 2. Detect config mechanism
    yield _ev("detect_config", "running", "Asking the LLM how this framework loads its column config…")
    cfg = llm_client.detect_framework_config(source_code, target_table, model=model)
    # De-dupe by name: the detection response is free-form LLM JSON and a repeated
    # table would otherwise be queried twice and named twice in the user-facing text.
    tables: list[dict] = []
    _seen_names: set[str] = set()
    for t in (cfg.get("config_tables") or []):
        if not isinstance(t, dict):
            continue
        nm = t.get("name")
        if not nm or nm in _seen_names:
            continue
        _seen_names.add(nm)
        tables.append(t)
    table_names = [t.get("name") for t in tables]
    params_wanted = cfg.get("parameters") or []
    key_cols = cfg.get("target_key_columns") or []
    if cfg.get("error"):
        yield _ev("detect_config", "error", f"Config detection failed: {cfg['error']}")
        yield {"type": "result", "columns": [], "derived": False, "reason_code": "detect_failed",
               "detail": "Could not detect the config mechanism."}
        return
    yield _ev("detect_config", "ok",
              f"Detected {len(tables)} config table(s) and {len(params_wanted)} parameter(s). {cfg.get('notes', '')}".strip(),
              config_tables=table_names, parameters=params_wanted)

    # 3. Parameters
    yield _ev("params", "running", "Reading the producer's parameters…")
    params = _fetch_entity_parameters(et, entity_id)
    picked = {k: params[k] for k in params_wanted if k in params} or params
    yield _ev("params", "ok" if picked else "warn",
              f"Found {len(picked)} parameter value(s)." if picked else "No matching parameter values found.",
              parameters=picked)

    # 4. Query config tables
    config_data: list[dict] = []
    empty_tables: list[str] = []       # identified tables that are currently empty
    unreadable_tables: list[str] = []  # identified tables we couldn't SELECT at all
    uncertain_empty: list[str] = []    # empty tables whose NAME was only an LLM guess
    for t in tables:
        name = t.get("name")
        yield _ev("query_config", "running", f"Querying config table {name}…")
        try:
            res = _query_config_table(name, target_table, key_cols)
        except Exception as e:
            unreadable_tables.append(name)
            yield _ev("query_config", "warn", f"Config table {name} isn't readable ({str(e)[:120]}) — skipping.")
            continue
        if res is None:
            unreadable_tables.append(name)
            yield _ev("query_config", "warn", f"Config table {name} is invalid or unreadable — skipping.")
            continue
        # An empty config table is a distinct, common case for frameworks that
        # write their config per-run (or truncate between runs): there is simply
        # nothing to derive from right now — call it out rather than proceeding to
        # a generic "no columns" failure.
        if res.get("total_rows", 0) == 0:
            empty_tables.append(name)
            # `certain: false` means the detection prompt only GUESSED this name
            # from a variable/parameter, so its emptiness proves nothing.
            if t.get("certain") is False:
                uncertain_empty.append(name)
            yield _ev("query_config", "warn", f"Config table {name} is currently empty — no config rows to derive from.")
            continue
        config_data.append(res)
        yield _ev("query_config", "ok",
                  f"{name}: {len(res['rows'])} relevant row(s)"
                  + (f" (filtered from {res['total_rows']} by target)" if res["matched"] else f" (sample of {res['total_rows']})") + ".")

    blockers = _blockers_note(empty_tables, unreadable_tables, uncertain_empty)

    # Short-circuit ONLY when emptiness provably explains the failure: every
    # identified table was read successfully, all of them came back empty, the
    # detection was CERTAIN about their names, and there are no parameters to
    # derive from instead. Any other combination (an unreadable table, a guessed
    # name, or usable parameters) falls through to the derive step below, which can
    # still derive from the source + parameters alone.
    if empty_tables and not config_data and not unreadable_tables and not uncertain_empty and not picked:
        names = ", ".join(empty_tables)
        yield {"type": "result", "columns": [], "derived": False, "reason_code": "config_empty",
               "config_tables": table_names, "empty_config_tables": empty_tables,
               "detail": (f"The config table(s) {names} are currently empty. This framework writes its column "
                          f"config per run, so run the producing {_entity_label(et)} for this target, "
                          f"then re-analyze.")}
        return

    if not tables:
        yield _ev("query_config", "warn", "No config tables were identified — deriving from source + parameters alone.")
    elif not config_data:
        # Tables were identified but none yielded rows, yet emptiness alone doesn't
        # explain it (see the short-circuit above) — say what's blocking, once, then
        # still attempt derivation from the source + parameters.
        yield _ev("query_config", "warn", " ".join(
            x for x in ("No usable config rows.", blockers, "Trying source + parameters instead.") if x))

    # 5. Derive columns
    yield _ev("derive", "running", "Deriving column transformations from the config + parameters…")
    target_cols = _fetch_target_columns(target_table)
    columns = llm_client.derive_columns_from_config(
        source_code=source_code, target_table=target_table, target_columns=target_cols,
        parameters=picked, config_data=config_data, model=model,
    )
    # Reject only if NOTHING derived carries real lineage; otherwise keep the full
    # set (don't drop individual columns a model happened to label UNKNOWN).
    columns = columns or []
    if not any(_is_meaningful_column(c) for c in columns):
        yield _ev("derive", "error", "No concrete columns could be derived from the available config (results were UNKNOWN).")
        yield {"type": "result", "columns": [], "derived": False, "reason_code": "no_columns",
               "config_tables": table_names, "empty_config_tables": empty_tables,
               "unreadable_config_tables": unreadable_tables,
               # Append the specific blockers so a failure never leaves the user
               # guessing between a grant problem and a stale-config problem.
               "detail": " ".join(x for x in ("No columns could be derived from the framework config.", blockers) if x)}
        return
    yield _ev("derive", "ok", f"Derived {len(columns)} column transformation(s) from config.")

    # 6. Save as a new analysis version
    version = None
    try:
        version = analysis_store.save_analysis(
            entity_type=et, entity_id=entity_id, source_code=source_code,
            target_table=target_table, analysis=columns,
            llm_model=(model or llm_client.LLM_MODEL_NAME) + " (framework-config)", actor=actor,
        )
        yield _ev("save", "ok", f"Saved as analysis version {version}.")
    except Exception as e:
        yield _ev("save", "warn", f"Derived columns but couldn't save a version ({str(e)[:120]}).")

    yield {
        "type": "result", "derived": True, "columns": columns, "version": version,
        "source": "llm", "derived_via": "framework_config",
        "config_tables": table_names, "empty_config_tables": empty_tables,
        "unreadable_config_tables": unreadable_tables,
    }
