"""Heuristic PySpark parsing (DataFrame ops + spark.sql blocks)."""

from __future__ import annotations

import re
from typing import Any

from transformation_lineage.parsing.sql_parser import parse_sql_text


# Broadened to match column names with dots, hyphens, and other non-word chars
# e.g. .withColumn("my-col", ...) or .withColumn("nested.field", ...)
_WITHCOL = re.compile(
    r'\.withColumn\(\s*["\']([^"\']+)["\']\s*,\s*(.+?)\s*\)',
    re.DOTALL,
)
_SELECT = re.compile(r'\.select\(\s*(.+?)\s*\)', re.DOTALL)
_READ = re.compile(r'(?:read|table)\(\s*["\']([\w.]+)["\']\s*\)')
_SPARK_TABLE_ASSIGN = re.compile(
    r'(\w+)\s*=\s*spark\.(?:read\.)?table\(\s*["\']([\w.]+)["\']\s*\)'
)
_WRITE_SAVEAS = re.compile(r'\.saveAsTable\(\s*["\']([\w.]+)["\']\s*\)')

# Top-level string assignments: `IDENT = "value"` or `IDENT = f"value"`, etc.
_STRING_ASSIGN = re.compile(
    r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*[bBfFrRuU]{0,2}["\']([^"\'\n]+)["\']\s*$',
    re.MULTILINE,
)

# spark.sql(...) blocks. Triple-quote variants are listed first because they
# would otherwise be partially matched by the single-quote patterns.
_SPARK_SQL_PATTERNS = [
    re.compile(r'spark\.sql\(\s*([fFrRbBuU]{0,3})"""(.+?)"""\s*\)', re.DOTALL),
    re.compile(r"spark\.sql\(\s*([fFrRbBuU]{0,3})'''(.+?)'''\s*\)", re.DOTALL),
    re.compile(r'spark\.sql\(\s*([fFrRbBuU]{0,3})"([^"\n]+)"\s*\)'),
    re.compile(r"spark\.sql\(\s*([fFrRbBuU]{0,3})'([^'\n]+)'\s*\)"),
]

_FSTRING_VAR = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)\}')


def _build_var_map(text: str) -> dict[str, str]:
    """Map DataFrame variable name -> fully-qualified source table."""
    return {m.group(1): m.group(2) for m in _SPARK_TABLE_ASSIGN.finditer(text)}


def _build_string_var_map(text: str) -> dict[str, str]:
    """Capture top-level `IDENT = "value"` assignments for f-string substitution."""
    return {m.group(1): m.group(2) for m in _STRING_ASSIGN.finditer(text)}


def _expand_fstring(sql: str, string_vars: dict[str, str]) -> str:
    """Replace `{VAR}` placeholders with values from `string_vars`."""
    if not string_vars:
        return sql

    def repl(m: re.Match[str]) -> str:
        return string_vars.get(m.group(1), m.group(0))

    return _FSTRING_VAR.sub(repl, sql)


def _infer_output_table(text: str) -> str | None:
    m = _WRITE_SAVEAS.search(text)
    return m.group(1) if m else None


def _extract_sql_blocks(text: str, *, string_vars: dict[str, str]) -> list[str]:
    """Pull SQL strings out of spark.sql(...) calls; expand f-string vars."""
    blocks: list[str] = []
    for pat in _SPARK_SQL_PATTERNS:
        for m in pat.finditer(text):
            prefix = (m.group(1) or "").lower()
            sql = m.group(2)
            if "f" in prefix:
                sql = _expand_fstring(sql, string_vars)
            blocks.append(sql)
    return blocks


def parse_pyspark_source(
    text: str,
    *,
    artifact_id: str,
    string_vars: dict[str, str] | None = None,
) -> dict[str, Any]:
    warnings: list[str] = []
    mappings: list[dict[str, Any]] = []
    tables: list[str] = []
    var_map = _build_var_map(text)

    local_string_vars = _build_string_var_map(text)
    if string_vars:
        merged = dict(string_vars)
        merged.update(local_string_vars)
        local_string_vars = merged

    output_table_fqn = _infer_output_table(text)

    for m in _READ.finditer(text):
        tables.append(m.group(1))
    tables.extend(var_map.values())

    # Single-source heuristic: if the file reads from exactly one table, every
    # unresolved col() reference almost certainly comes from that table.
    single_source_fqn = next(iter(set(var_map.values()))) if len(set(var_map.values())) == 1 else None

    for m in _WITHCOL.finditer(text):
        col = m.group(1)
        expr = m.group(2).strip()
        # Match col references with word-char names plus dots/hyphens
        for ref in re.findall(r'(?:col|F\.col)\(\s*["\']([\w.\-]+)["\']\s*\)', expr):
            mappings.append(
                {
                    "artifact_id": artifact_id,
                    "output_column": col,
                    "source_ref": ref,
                    "source_fqn": single_source_fqn,
                    "source_column": ref,
                    "expr": expr[:2000],
                    "expr_lang": "pyspark",
                }
            )

    for m in _SELECT.finditer(text):
        inner = m.group(1)
        for piece in inner.split(","):
            piece = piece.strip()
            alias_m = re.match(
                r'(?:col|F\.col)\(\s*["\']([\w.\-]+)["\']\s*\)\.alias\(\s*["\']([\w.\-]+)["\']\s*\)',
                piece,
            )
            if alias_m:
                mappings.append(
                    {
                        "artifact_id": artifact_id,
                        "output_column": alias_m.group(2),
                        "source_ref": alias_m.group(1),
                        "source_fqn": single_source_fqn,
                        "source_column": alias_m.group(1),
                        "expr": piece[:2000],
                        "expr_lang": "pyspark",
                    }
                )

    sql_stmts_parsed = 0
    for sql in _extract_sql_blocks(text, string_vars=local_string_vars):
        parsed = parse_sql_text(sql, artifact_id=artifact_id)
        mappings.extend(parsed.get("column_mappings") or [])
        tables.extend(parsed.get("table_references") or [])
        if output_table_fqn is None and parsed.get("output_table_fqn"):
            output_table_fqn = parsed["output_table_fqn"]
        sql_stmts_parsed += int(parsed.get("statements_parsed") or 0)
        warnings.extend(parsed.get("warnings") or [])

    stmts = (
        len(list(_WITHCOL.finditer(text)))
        + len(list(_SELECT.finditer(text)))
        + len(list(_READ.finditer(text)))
        + sql_stmts_parsed
    )
    return {
        "artifact_id": artifact_id,
        "language": "python",
        "statements_parsed": 1 if stmts else 0,
        "statements_skipped": 0 if stmts else 1,
        "column_mappings": mappings,
        "table_references": sorted(set(tables)),
        "output_table_fqn": output_table_fqn,
        "warnings": warnings,
    }


def parse_pyspark_cells(cells: list[dict[str, Any]], *, artifact_id: str) -> dict[str, Any]:
    # Pre-pass: gather string vars from every python cell
    global_string_vars: dict[str, str] = {}
    for c in cells:
        if str(c.get("language", "")).lower() not in ("python", "py", ""):
            continue
        global_string_vars.update(_build_string_var_map(str(c.get("source") or "")))

    merged: dict[str, Any] = {
        "artifact_id": artifact_id,
        "language": "python",
        "statements_parsed": 0,
        "statements_skipped": 0,
        "column_mappings": [],
        "table_references": [],
        "output_table_fqn": None,
        "warnings": [],
    }
    for c in cells:
        if str(c.get("language", "")).lower() not in ("python", "py", ""):
            continue
        src = str(c.get("source") or "")
        part = parse_pyspark_source(
            src, artifact_id=artifact_id, string_vars=global_string_vars
        )
        merged["statements_parsed"] += part["statements_parsed"]
        merged["statements_skipped"] += part["statements_skipped"]
        merged["column_mappings"].extend(part["column_mappings"])
        merged["table_references"].extend(part["table_references"])
        merged["warnings"].extend(part["warnings"])
        if merged["output_table_fqn"] is None and part.get("output_table_fqn"):
            merged["output_table_fqn"] = part["output_table_fqn"]
    merged["table_references"] = sorted(set(merged["table_references"]))
    return merged
