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
# Keys a config row/object might use to name the TARGET it applies to.
_TARGET_KEYS = ("target_name", "target", "name", "output_table", "target_table", "entity", "entity_name", "dataset")


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


def _focus_on_target(obj: object, target: str) -> tuple[object, bool]:
    """Recursively prune lists of target-describing dicts down to the entry(ies)
    matching `target` (normalized). Returns (pruned, matched). Lets a multi-target
    config collapse to just the relevant target's columns."""
    matched = False
    if isinstance(obj, dict):
        out = {}
        for k, val in obj.items():
            pv, m = _focus_on_target(val, target)
            out[k] = pv
            matched = matched or m
        return out, matched
    if isinstance(obj, list):
        keyed = [x for x in obj if isinstance(x, dict) and any(k in x for k in _TARGET_KEYS)]
        use_list = obj
        if keyed:
            keep = [x for x in keyed
                    if any(_norm_name(x[k]) == target for k in _TARGET_KEYS if k in x)]
            if keep:
                use_list, matched = keep, True
        out = []
        for x in use_list:
            pv, m = _focus_on_target(x, target)
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
    the target table's short name against target-key columns. Returns None if the
    table can't be read."""
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

    # Decode any structured (base64/JSON) config cells, then focus each row on the
    # target — so a multi-target config collapses to just the relevant columns and
    # the LLM never sees an opaque blob.
    focused_rows: list[tuple[dict, bool]] = []
    for r in rows:
        decoded = {k: _maybe_decode(v) for k, v in r.items()}
        pruned, m = _focus_on_target(decoded, target_norm)
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


def _is_table_not_found(err: Optional[Exception]) -> bool:
    """True when a config-table read failed because the table/schema doesn't
    exist (as opposed to a permissions error) — the signal that the LLM likely
    guessed the wrong schema and we should resolve the real location."""
    msg = str(err or "").upper()
    return any(s in msg for s in ("TABLE_OR_VIEW_NOT_FOUND", "SCHEMA_NOT_FOUND", "NOT_FOUND"))


def _resolve_config_alternates(name: str, target_table: str) -> list[str]:
    """Resolve a config table's REAL location(s) by its bare name.

    `detect_framework_config` is asked for a fully-qualified name, but when the
    framework code references the table unqualified (or builds its name from a
    parameter) the LLM tends to assume it shares the *target table's*
    catalog.schema — which is frequently wrong (config commonly lives in a
    sibling schema). Look the bare table name up in `information_schema.tables`
    within the target's catalog and return the concrete FQN(s) found, excluding
    the name we already tried. Best-effort: returns [] on any failure."""
    bare = (name or "").split(".")[-1]
    parts = (target_table or "").split(".")
    if not re.fullmatch(r"[A-Za-z0-9_]+", bare or "") or len(parts) != 3:
        return []
    catalog = parts[0]
    out: list[str] = []
    try:
        from backend.lineage_service import _get_client, _execute_sql
        client = _get_client()
        if client is None:
            return []
        rows = _execute_sql(
            client,
            f"SELECT table_schema FROM {catalog}.information_schema.tables "
            f"WHERE table_name = '{bare}' ORDER BY table_schema",
        )
        for r in rows or []:
            sch = r.get("table_schema")
            if not sch:
                continue
            fqn = f"{catalog}.{sch}.{bare}"
            if fqn != name and fqn not in out:
                out.append(fqn)
    except Exception as e:
        logger.info(f"framework_analysis: config-table resolution failed for '{name}': {e}")
    return out


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
        yield {"type": "result", "columns": [], "derived": False, "detail": "No source code to analyse."}
        return
    yield _ev("fetch_source", "ok", f"Loaded framework source ({len(source_code):,} chars).")

    # 2. Detect config mechanism
    yield _ev("detect_config", "running", "Asking the LLM how this framework loads its column config…")
    cfg = llm_client.detect_framework_config(source_code, target_table, model=model)
    tables = [t for t in (cfg.get("config_tables") or []) if isinstance(t, dict) and t.get("name")]
    params_wanted = cfg.get("parameters") or []
    key_cols = cfg.get("target_key_columns") or []
    if cfg.get("error"):
        yield _ev("detect_config", "error", f"Config detection failed: {cfg['error']}")
        yield {"type": "result", "columns": [], "derived": False, "detail": "Could not detect the config mechanism."}
        return
    yield _ev("detect_config", "ok",
              f"Detected {len(tables)} config table(s) and {len(params_wanted)} parameter(s). {cfg.get('notes', '')}".strip(),
              config_tables=[t.get("name") for t in tables], parameters=params_wanted)

    # 3. Parameters
    yield _ev("params", "running", "Reading the producer's parameters…")
    params = _fetch_entity_parameters(et, entity_id)
    picked = {k: params[k] for k in params_wanted if k in params} or params
    yield _ev("params", "ok" if picked else "warn",
              f"Found {len(picked)} parameter value(s)." if picked else "No matching parameter values found.",
              parameters=picked)

    # 4. Query config tables
    config_data: list[dict] = []
    empty_tables: list[str] = []   # identified config tables that are currently empty
    for t in tables:
        name = t.get("name")
        yield _ev("query_config", "running", f"Querying config table {name}…")
        res, err = None, None
        try:
            res = _query_config_table(name, target_table, key_cols)
        except Exception as e:
            err = e
        used = name
        # The LLM often qualifies an unqualified config table with the TARGET's
        # schema, which may not exist. When the read fails because the table/schema
        # isn't found, resolve the real location by its bare name and retry —
        # rather than giving up and deriving nothing.
        if res is None and _is_table_not_found(err):
            for alt in _resolve_config_alternates(name, target_table):
                yield _ev("query_config", "running", f"{name} not found there — resolving to {alt}…")
                try:
                    res = _query_config_table(alt, target_table, key_cols)
                except Exception as e:
                    err, res = e, None
                    continue
                if res is not None:
                    used = alt
                    yield _ev("query_config", "ok", f"Resolved {name} → {alt} (config lives in a different schema).")
                    break
        if res is None:
            detail = f" ({str(err)[:120]})" if err else ""
            yield _ev("query_config", "warn", f"Config table {name} isn't readable{detail} — skipping.")
            continue
        # An empty config table is a distinct, common case for frameworks that
        # write their config per-run (or truncate between runs): there is simply
        # nothing to derive from right now — call it out rather than proceeding to
        # a generic "no columns" failure.
        if res["total_rows"] == 0:
            empty_tables.append(used)
            yield _ev("query_config", "warn", f"Config table {used} is currently empty — no config rows to derive from.")
            continue
        config_data.append(res)
        yield _ev("query_config", "ok",
                  f"{used}: {len(res['rows'])} relevant row(s)"
                  + (f" (filtered from {res['total_rows']} by target)" if res["matched"] else f" (sample of {res['total_rows']})") + ".")

    # Short-circuit: config table(s) were identified but every one is empty, so
    # there is provably nothing to derive. Give an actionable, specific reason
    # (which the panel surfaces) instead of running the LLM and reporting a
    # generic "no columns" failure.
    if tables and not config_data and empty_tables:
        names = ", ".join(empty_tables)
        yield _ev("derive", "error", f"The config table(s) {names} are currently empty — nothing to derive from.")
        yield {"type": "result", "columns": [], "derived": False, "reason_code": "config_empty",
               "config_tables": empty_tables,
               "detail": (f"The config table(s) {names} are currently empty. This framework writes its column "
                          f"config per run, so run the producing {et.lower()} for this target, then re-analyze.")}
        return

    if not tables:
        yield _ev("query_config", "warn", "No config tables were identified — deriving from source + parameters alone.")

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
        yield {"type": "result", "columns": [], "derived": False, "detail": "No columns could be derived from the framework config."}
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
        "config_tables": [t.get("name") for t in tables],
    }
