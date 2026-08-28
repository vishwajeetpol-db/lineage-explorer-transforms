"""LLM client — used by producer_source.py (capability 27).

Calls the Databricks Foundation Model API (served_model_name or external
endpoint configured via env vars) to analyse notebook/query source code
and infer per-column transformation descriptions.

What leaves the app: producer SOURCE CODE (notebook / query text) plus table and
column names. The endpoint is constrained to this workspace — see
_resolve_endpoint_url — so a misconfigured env var cannot turn that into egress.

Config env vars:
  LLM_ENDPOINT_URL     — serving-endpoint path, or an absolute URL on THIS
                         workspace's host. Anything else is refused and the
                         in-workspace default is used.
  LLM_MODEL_NAME       — model name to pass in the request body.
                         Default: databricks-meta-llama-3-1-70b-instruct
  LLM_API_TOKEN        — bearer token. Default: SPN token from DATABRICKS_TOKEN.
  LLM_MAX_TOKENS       — max completion tokens. Default: 1024.
  LLM_TIMEOUT_SECONDS  — HTTP timeout. Default: 60.
"""
from __future__ import annotations

import os
import json
import logging
import textwrap
import threading as _threading
from datetime import datetime as _dt
from typing import Optional

logger = logging.getLogger(__name__)

# Default to a Foundation Model endpoint that exists in the workspace. The old
# default (databricks-meta-llama-3-1-70b-instruct) is not provisioned in newer
# workspaces; databricks-claude-sonnet-4-6 is a broadly-available FMAPI endpoint.
# Override with LLM_MODEL_NAME.
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "databricks-claude-sonnet-4-6")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4000"))
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Where source code is allowed to go
#
# Every call here ships PRODUCER SOURCE CODE — notebook and query text — plus table
# and column names. That routinely contains regulated identifiers, so the honest
# answer to "what leaves this app, and to where" has to be something narrower than
# "whatever LLM_ENDPOINT_URL points at". The override was unvalidated, and the SDK
# attaches the workspace credential to whatever it is given, so a single
# misconfigured env var could ship source code off-workspace with a valid token.
#
# The override now has to resolve to this workspace: either a relative serving path
# or an absolute URL on the workspace host. Anything else is refused and the
# in-workspace default is used instead — a config mistake must not become an egress.
# ---------------------------------------------------------------------------
_ALLOWED_URL_PREFIXES = ("/serving-endpoints/", "/api/2.0/serving-endpoints/")


class LLMBudgetError(RuntimeError):
    """A per-user LLM call budget was exhausted; back off rather than retry now."""


def _resolve_endpoint_url(endpoint: str) -> str:
    """Return the URL to POST to, refusing an off-workspace override."""
    override = (os.environ.get("LLM_ENDPOINT_URL") or "").strip()
    if not override:
        return f"/serving-endpoints/{endpoint}/invocations"

    if override.startswith("/"):
        if override.startswith(_ALLOWED_URL_PREFIXES):
            return override
        logger.error(
            "LLM_ENDPOINT_URL=%r is not a serving-endpoint path; ignoring it and "
            "using the in-workspace endpoint. Allowed prefixes: %s",
            override[:120], ", ".join(_ALLOWED_URL_PREFIXES),
        )
        return f"/serving-endpoints/{endpoint}/invocations"

    # Absolute URL: only this workspace's own host is acceptable.
    from urllib.parse import urlsplit
    try:
        from backend.lineage_service import _get_client
        host = urlsplit(_get_client().config.host or "").hostname or ""
    except Exception:
        host = ""
    parts = urlsplit(override)
    if parts.scheme == "https" and host and parts.hostname == host:
        return override
    logger.error(
        "LLM_ENDPOINT_URL host %r is not this workspace (%r); ignoring it and using "
        "the in-workspace endpoint. Source code must not leave the workspace via an "
        "env override.",
        parts.hostname, host or "unknown",
    )
    return f"/serving-endpoints/{endpoint}/invocations"


# ---------------------------------------------------------------------------
# Per-user call budget
#
# The deep-analysis path is agentic and streaming: one user action can be many model
# calls. Nothing bounded that, per user or in total. The actor comes from the
# warehouse_gate attribution ContextVar, which middleware stamps once per request and
# which propagates through asyncio.to_thread — so the budget follows the request
# without every call site having to thread an identity through.
# ---------------------------------------------------------------------------
LLM_MAX_CALLS_PER_USER_PER_DAY = int(os.environ.get("LLM_MAX_CALLS_PER_USER_PER_DAY", "500"))

_llm_budget_lock = _threading.Lock()
_llm_calls: dict[str, int] = {}
_llm_budget_day: str = ""


def _charge_llm_budget() -> None:
    global _llm_budget_day
    from backend.warehouse_gate import current_actor
    actor = current_actor() or "anon"
    today = _dt.now().strftime("%Y-%m-%d")
    with _llm_budget_lock:
        if _llm_budget_day != today:
            _llm_budget_day = today
            _llm_calls.clear()
        used = _llm_calls.get(actor, 0)
        if used >= LLM_MAX_CALLS_PER_USER_PER_DAY:
            raise LLMBudgetError(
                f"You have used the daily limit of {LLM_MAX_CALLS_PER_USER_PER_DAY} "
                f"AI analysis calls. It resets at midnight."
            )
        _llm_calls[actor] = used + 1


def get_llm_budget() -> dict:
    """Budget state, for /api/diagnostics and the admin dashboard."""
    with _llm_budget_lock:
        return {
            "day": _llm_budget_day or _dt.now().strftime("%Y-%m-%d"),
            "max_per_user_per_day": LLM_MAX_CALLS_PER_USER_PER_DAY,
            "distinct_users": len(_llm_calls),
            "calls_today": sum(_llm_calls.values()),
            "top_users": sorted(_llm_calls.items(), key=lambda kv: -kv[1])[:10],
        }


def _reset_llm_budget() -> None:
    """Zero the budget — for tests only."""
    global _llm_budget_day
    with _llm_budget_lock:
        _llm_calls.clear()
        _llm_budget_day = ""


_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a data-lineage analyst. You are given source code (SQL, Python, or PySpark)
    from a data pipeline that writes to a specific table. Your task is to infer, for
    each output column of that table, what input columns it is derived from and what
    transformation logic was applied.

    Respond ONLY with a JSON array. Each element must have these keys:
        - "target_column"   : the output column name (string)
        - "source_columns"  : list of input column names (may be empty if the value is
                              a constant or system-generated)
        - "expression"      : a concise SQL/PySpark expression describing the transformation
        - "category"        : one of PASS_THROUGH, ARITHMETIC, STRING, CAST, AGGREGATE,
                              WINDOW, LOOKUP, CONDITIONAL, HASH, CONSTANT, UNKNOWN
        - "confidence"      : 0.0–1.0 reflecting how certain you are

    If you cannot determine a column's lineage from the source code, include it with
    category UNKNOWN and confidence 0.0. Do not output anything other than the JSON array.
""")


def analyze_source_code(
    source_code: str,
    target_table: str,
    target_columns: Optional[list[str]] = None,
    model: Optional[str] = None,
) -> list[dict]:
    """Call the LLM to infer per-column transformations from `source_code`.

    Returns a list of column analysis dicts (see _SYSTEM_PROMPT for schema).
    Returns [] on any error — never raises.
    """
    if not source_code or not source_code.strip():
        logger.info("llm: empty source_code, skipping LLM call")
        return []

    col_hint = ""
    if target_columns:
        col_hint = (
            f"\n\nThe target table `{target_table}` has these output columns: "
            f"{', '.join(target_columns)}.\n"
            f"You MUST return exactly one entry for EVERY one of these {len(target_columns)} columns, "
            f"in the same order. For columns that are copied unchanged from an upstream column, "
            f"still include them with category PASS_THROUGH and the source column name — do not omit them."
        )

    user_message = (
        f"Analyse the following source code that writes to `{target_table}`.{col_hint}\n\n"
        f"```\n{source_code[:12000]}\n```"
    )

    # Route through _invoke_chat so the App's OAuth client is used and models that
    # reject `temperature` are retried transparently. NOTE: invocation/parse errors
    # PROPAGATE (we no longer swallow to []) so the caller can tell a real LLM
    # failure (endpoint down, bad request, unparseable output) apart from a model
    # that simply found no columns — and surface a specific message either way.
    # _invoke_chat raises on a REAL invocation failure (endpoint down, bad request
    # for the model, timeout) — that propagates so the caller surfaces a specific
    # error. But if the call SUCCEEDS and the model just returns empty/unparseable
    # output (common when generic framework code has no column logic), treat it as
    # "no columns" so the deep-framework fallback kicks in rather than a hard error.
    content = _invoke_chat(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        model=model,
    )
    try:
        parsed = _parse_json_content(content)
    except Exception as e:
        logger.info(f"llm: unparseable analysis output for {target_table}: {e}")
        return []
    # The model may return either a bare JSON array or {"columns": [...]}.
    if isinstance(parsed, dict):
        return parsed.get("columns", [])
    return parsed if isinstance(parsed, list) else []


_OVERVIEW_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a senior data engineer explaining a table's column-level transformation
    lineage to a data analyst in plain English. You are given a JSON array of the
    table's columns; each has: target_column, source_columns, expression, category.

    Respond ONLY with a JSON object of this exact shape:
        {
          "summary": "<2-3 sentence plain-English overview of what this table's
                      transformations do as a whole (the main joins, derivations,
                      and any notable business logic)>",
          "columns": [
            {"column": "<target_column>",
             "explanation": "<1-2 sentence plain-English description of how this
                             column is produced, naming its source columns and the
                             logic; do NOT just restate the raw expression>"}
          ]
        }

    Include EVERY input column exactly once, in the same order. Keep it concise and
    non-technical where possible. Output JSON only — no markdown, no prose outside JSON.
""")


def explain_transformations(
    columns: list[dict],
    target_table: str,
    model: Optional[str] = None,
) -> dict:
    """Produce a plain-English overview of a table's column transformations.

    Given the already-resolved column list (from a captured plan or LLM analysis),
    returns {"summary": str, "columns": [{"column", "explanation"}]}. Never raises;
    returns an `error` key on failure so the caller can surface it.
    """
    if not columns:
        return {"summary": "", "columns": []}

    compact = [
        {
            "target_column": c.get("target_column") or c.get("column"),
            "source_columns": c.get("source_columns") or [],
            "expression": c.get("expression") or c.get("transformation") or "",
            "category": c.get("category"),
        }
        for c in columns
    ]
    user_message = (
        f"Explain the column transformations for table `{target_table}`:\n\n"
        f"```json\n{json.dumps(compact)[:12000]}\n```"
    )
    try:
        content = _invoke_chat(
            [
                {"role": "system", "content": _OVERVIEW_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            model=model, temperature=0.1,
        )
        parsed = _parse_json_content(content)
        if isinstance(parsed, dict):
            return {"summary": parsed.get("summary", ""), "columns": parsed.get("columns", [])}
        return {"summary": "", "columns": parsed if isinstance(parsed, list) else []}
    except Exception as e:
        logger.info(f"llm: overview failed for {target_table}: {e}")
        return {"summary": "", "columns": [], "error": str(e)}


_GRAPH_EXPLAIN_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a data analyst explaining a data-lineage diagram to a NON-TECHNICAL
    business audience. You are given the nodes and connections of a lineage graph
    for a focus dataset. Nodes are either datasets (tables/views/files) or
    processing steps (jobs/pipelines/code). A connection "A -> B" means A feeds
    into B.

    Explain, in plain business English (no SQL, no jargon, no technical IDs):
      - what the focus dataset is and where its data ultimately comes from,
      - how the data flows and is transformed along the way,
      - what the key processing steps do,
      - what depends on / consumes the focus dataset.

    Respond ONLY with a JSON object of this exact shape:
      {
        "summary": "<2-4 sentence plain-English overview of the whole flow>",
        "steps": [
          {"title": "<short stage label, e.g. 'Raw orders arrive'>",
           "detail": "<1-2 sentence plain-English description of this stage>"}
        ]
      }
    Order steps from data sources → processing → the focus dataset → consumers.
    Keep it concise (at most 8 steps). Output JSON only — no markdown, no prose
    outside the JSON.
""")


def explain_lineage_graph(
    nodes: list[dict],
    edges: list[dict],
    focus_table: str,
    detail: str = "data_and_processing",
    model: Optional[str] = None,
) -> dict:
    """Plain-English narrative of a whole lineage graph for a business audience.

    `nodes` are [{"id","label","type"}] and `edges` are [{"source","target"}]
    referencing node ids. Returns {"summary": str, "steps": [{"title","detail"}]};
    never raises — returns an `error` key on failure so the caller can surface it.
    """
    if not nodes:
        return {"summary": "", "steps": []}

    # Build an id-free, plain description so the LLM never sees technical ids.
    id_to_label = {n.get("id"): n.get("label") for n in nodes}
    node_lines = [f"- {n.get('label')} ({n.get('type')})" for n in nodes if n.get("label")]
    edge_lines = []
    for e in edges or []:
        s = id_to_label.get(e.get("source"))
        t = id_to_label.get(e.get("target"))
        if s and t:
            edge_lines.append(f"- {s} -> {t}")
    view = "datasets and processing steps" if detail == "data_and_processing" else "datasets only"
    user_message = (
        f"Focus dataset: {focus_table}\n"
        f"View: {view}\n\n"
        f"NODES:\n" + "\n".join(node_lines[:200]) + "\n\n"
        f"CONNECTIONS (feeds into):\n" + ("\n".join(edge_lines[:400]) or "- (none)")
    )
    try:
        content = _invoke_chat(
            [
                {"role": "system", "content": _GRAPH_EXPLAIN_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            model=model, temperature=0.1,
        )
        parsed = _parse_json_content(content)
        if isinstance(parsed, dict):
            return {"summary": parsed.get("summary", ""), "steps": parsed.get("steps") or []}
        return {"summary": "", "steps": []}
    except Exception as e:
        logger.info(f"llm: graph explanation failed for {focus_table}: {e}")
        return {"summary": "", "steps": [], "error": str(e)}


def _invoke_chat(messages: list[dict], model: Optional[str] = None, temperature: float = 0.0) -> str:
    """Low-level chat call → raw assistant content string. Raises on failure.

    Some serving endpoints (e.g. certain Claude/reasoning models via Bedrock)
    reject the optional `temperature` parameter with a BAD_REQUEST. When that
    happens we transparently retry once without it, so model choice never breaks
    analysis."""
    from backend.lineage_service import _get_client
    _charge_llm_budget()
    client = _get_client()
    endpoint = model or LLM_MODEL_NAME
    url = _resolve_endpoint_url(endpoint)

    def _do(payload: dict):
        return client.api_client.do("POST", url, body=payload)

    payload = {"messages": messages, "max_tokens": LLM_MAX_TOKENS, "temperature": temperature}
    try:
        data = _do(payload)
    except Exception as e:
        # Retry without unsupported params (temperature / max_tokens) if the model
        # complains about them specifically; otherwise re-raise.
        msg = str(e).lower()
        if "temperature" in msg or ("max_tokens" in msg and "support" in msg):
            payload.pop("temperature", None)
            if "max_tokens" in msg and "support" in msg:
                payload.pop("max_tokens", None)
            data = _do(payload)
        else:
            raise
    content = data["choices"][0]["message"]["content"]
    # Anthropic/Claude-style endpoints return content as a list of blocks
    # (e.g. [{"type":"text","text":"..."}]) rather than a plain string; flatten
    # to text so downstream parsing (`.strip()` / JSON) works for every model.
    if isinstance(content, list):
        content = "".join(
            (b.get("text") or "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return content or ""


def _parse_json_content(content: str):
    """Parse a model response into JSON, tolerating ```json fences and prose that
    wraps the JSON (some models prepend a sentence before the array/object). Raises
    only when no JSON value can be found."""
    s = (content or "").strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(s)
    except Exception:
        # Salvage the outermost JSON array/object embedded in surrounding text.
        starts = [i for i in (s.find("["), s.find("{")) if i != -1]
        start = min(starts) if starts else -1
        end = max(s.rfind("]"), s.rfind("}"))
        if start != -1 and end > start:
            return json.loads(s[start:end + 1])
        raise


_FRAMEWORK_DETECT_PROMPT = textwrap.dedent("""\
    You are analysing METADATA-DRIVEN / CONFIGURABLE ETL framework code. This code
    does NOT hard-code column transformations; it reads its mapping/config from
    configuration TABLES and/or runtime PARAMETERS and applies them generically.

    From the source code, identify:
      - config_tables: fully-qualified (catalog.schema.table) names of config /
        metadata / mapping tables the code READS to obtain column mappings or
        rules. If a name is built from a variable/parameter, give your best
        concrete guess and set "certain": false.
      - parameters: names of pipeline/job parameters, widgets, or spark confs the
        code reads that select/filter which config applies.
      - target_key_columns: column names IN those config tables that identify which
        TARGET table/entity a config row applies to (e.g. target_table, entity_name).
      - notes: 1-2 sentences on how config rows map to output columns.

    Respond ONLY with JSON:
    {"config_tables":[{"name":"cat.sch.tbl","certain":true}],
     "parameters":["..."], "target_key_columns":["..."], "notes":"..."}
""")


def detect_framework_config(source_code: str, target_table: str, model: Optional[str] = None) -> dict:
    """Ask the LLM how a metadata-driven framework obtains its column config.
    Returns {config_tables:[{name,certain}], parameters:[], target_key_columns:[], notes}."""
    if not source_code.strip():
        return {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": ""}
    try:
        content = _invoke_chat([
            {"role": "system", "content": _FRAMEWORK_DETECT_PROMPT},
            {"role": "user", "content": f"Target table: `{target_table}`.\n\n```\n{source_code[:12000]}\n```"},
        ], model=model)
        parsed = _parse_json_content(content)
        if not isinstance(parsed, dict):
            return {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": ""}
        return {
            "config_tables": parsed.get("config_tables") or [],
            "parameters": parsed.get("parameters") or [],
            "target_key_columns": parsed.get("target_key_columns") or [],
            "notes": parsed.get("notes") or "",
        }
    except Exception as e:
        logger.info(f"llm: framework config detection failed for {target_table}: {e}")
        return {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": "", "error": str(e)}


_DERIVE_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a data-lineage analyst working on a METADATA-DRIVEN ETL framework. The
    generic engine code contains no explicit column logic — the real per-column
    mappings come from CONFIG TABLE rows and PARAMETERS provided below. Use the
    config rows as the source of truth to reconstruct, for each output column of
    the target table, its source columns and transformation.

    Respond ONLY with a JSON array. Each element must have:
        - "target_column"  : output column name
        - "source_columns" : list of input column names (may be empty for constants)
        - "expression"     : concise SQL/PySpark expression for the transformation
        - "category"       : one of PASS_THROUGH, ARITHMETIC, STRING, CAST, AGGREGATE,
                             WINDOW, LOOKUP, CONDITIONAL, HASH, CONSTANT, UNKNOWN
        - "confidence"     : 0.0-1.0
    Base every entry on the config rows/parameters. Output the JSON array only.
""")


def derive_columns_from_config(
    source_code: str, target_table: str, target_columns: Optional[list[str]],
    parameters: dict, config_data: list[dict], model: Optional[str] = None,
) -> list[dict]:
    """Second-pass analysis: derive per-column transformations from framework
    config rows + parameters (used when direct source analysis found nothing)."""
    col_hint = ""
    if target_columns:
        col_hint = (
            f"\n\nThe target table has these output columns: {', '.join(target_columns)}. "
            f"Return exactly one entry for EVERY one of them."
        )
    user_message = (
        f"Target table: `{target_table}`.{col_hint}\n\n"
        f"PARAMETERS:\n{json.dumps(parameters)[:2500]}\n\n"
        f"CONFIG TABLE DATA:\n{json.dumps(config_data)[:9000]}\n\n"
        f"FRAMEWORK SOURCE (excerpt):\n```\n{source_code[:6000]}\n```"
    )
    try:
        content = _invoke_chat([
            {"role": "system", "content": _DERIVE_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ], model=model, temperature=0.0)
        parsed = _parse_json_content(content)
        if isinstance(parsed, dict):
            return parsed.get("columns", [])
        return parsed if isinstance(parsed, list) else []
    except Exception as e:
        logger.info(f"llm: config-based derivation failed for {target_table}: {e}")
        return []


def is_llm_configured() -> bool:
    """Return True if we can reach a serving endpoint.

    In a Databricks App the SP authenticates via OAuth through the SDK client,
    so no static token is required — a usable WorkspaceClient is sufficient.
    """
    try:
        from backend.lineage_service import _get_client
        return _get_client() is not None
    except Exception:
        return False
