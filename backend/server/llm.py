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

# Default to a Foundation Model endpoint that exists in the workspace. The old
# default (databricks-meta-llama-3-1-70b-instruct) is not provisioned in newer
# workspaces; databricks-claude-sonnet-4-6 is a broadly-available FMAPI endpoint.
# Override with LLM_MODEL_NAME.
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "databricks-claude-sonnet-4-6")
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "4000"))
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "60"))


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

    payload = {
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "max_tokens": LLM_MAX_TOKENS,
        "temperature": 0.0,
    }

    try:
        # Route through the SDK's authenticated client so this works with the App's
        # OAuth service principal (no static bearer token needed). An explicit
        # LLM_ENDPOINT_URL override still wins for external / custom endpoints.
        from backend.lineage_service import _get_client
        client = _get_client()
        endpoint = model or LLM_MODEL_NAME
        override_url = os.environ.get("LLM_ENDPOINT_URL", "")
        if override_url:
            data = client.api_client.do("POST", override_url, body=payload)
        else:
            data = client.api_client.do(
                "POST", f"/serving-endpoints/{endpoint}/invocations", body=payload
            )
        content = data["choices"][0]["message"]["content"]
        # Strip markdown code fences if present
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.split("\n", 1)[-1].rsplit("```", 1)[0]
        parsed = json.loads(stripped)
        # The model may return either a bare JSON array or {"columns": [...]}.
        if isinstance(parsed, dict):
            return parsed.get("columns", [])
        return parsed
    except Exception as e:
        logger.info(f"llm: analysis failed for {target_table}: {e}")
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
