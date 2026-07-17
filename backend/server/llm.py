"""LLM client — used by producer_source.py (capability 27).

Calls the Databricks Foundation Model API (served_model_name or external
endpoint configured via env vars) to analyse notebook/query source code
and infer per-column transformation descriptions.

Config env vars:
  LLM_ENDPOINT_URL     — full URL to the /chat/completions endpoint.
                         Default: Databricks FMAPI endpoint in this workspace.
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
from typing import Optional

logger = logging.getLogger(__name__)

LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "databricks-meta-llama-3-1-70b-instruct")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "1024"))
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))


def _get_endpoint_url() -> str:
    url = os.environ.get("LLM_ENDPOINT_URL", "")
    if url:
        return url
    # Default: Databricks workspace FMAPI
    from backend.lineage_service import _get_client
    host = _get_client().config.host.rstrip("/")
    return f"{host}/serving-endpoints/{LLM_MODEL_NAME}/invocations"


def _get_api_token() -> str:
    token = os.environ.get("LLM_API_TOKEN", "")
    if token:
        return token
    token = os.environ.get("DATABRICKS_TOKEN", "")
    if token:
        return token
    try:
        from backend.lineage_service import _get_client
        return _get_client().config.token or ""
    except Exception:
        return ""


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
        col_hint = f"\n\nThe target table `{target_table}` has these output columns: {', '.join(target_columns)}."

    user_message = (
        f"Analyse the following source code that writes to `{target_table}`.{col_hint}\n\n"
        f"```\n{source_code[:12000]}\n```"
    )

    payload = {
        "model": LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.0,
    }

    try:
        import urllib.request
        req = urllib.request.Request(
            _get_endpoint_url(),
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {_get_api_token()}",
            },
            method="POST",
        )
        import socket
        ctx = None
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
            body = resp.read().decode()
        data = json.loads(body)
        content = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(stripped)
    except Exception as e:
        logger.info(f"llm: analysis failed for {target_table}: {e}")
        return []


def is_llm_configured() -> bool:
    """Return True if a token and reachable endpoint are available."""
    token = _get_api_token()
    url = _get_endpoint_url()
    return bool(token and url)
