"""Parse a normalized notebook artifact into merged parse dictionaries.

Python cells are parsed by the AST-based extractor first
(`parse_pyspark_cells_ast`); the regex parser is only consulted when AST
returned no mappings AND no table references, which is the natural fallback
for cells that don't compile (notebook scratch, half-finished code) or that
use idioms the AST walker doesn't yet recognize.
"""

from __future__ import annotations

import json
import re
from typing import Any

from transformation_lineage.parsing.pyspark_ast_parser import parse_pyspark_cells_ast
from transformation_lineage.parsing.pyspark_parser import parse_pyspark_cells
from transformation_lineage.parsing.sql_parser import (
    _qualify_fqn,
    detect_use_directives,
    parse_sql_text,
)

# `spark.sql("USE CATALOG x")` / `spark.sql(f"USE SCHEMA {SCHEMA}")` set the
# active catalog/schema from *Python* cells — the dominant notebook idiom — so
# their target is written bare (`saveAsTable("gold_x")`). detect_use_directives
# only sees literal `USE` SQL text, so first inline any simple string variable
# (`CATALOG = "main"`) into the USE statement before scanning.
_PY_ASSIGN_STR_RE = re.compile(
    r"""^\s*([A-Za-z_]\w*)\s*=\s*["']([^"']+)["']\s*$""", re.MULTILINE
)
_USE_IN_PY_RE = re.compile(
    r"""\bUSE\s+(CATALOG|SCHEMA|DATABASE)\s+\{?([A-Za-z_]\w*)\}?""", re.IGNORECASE
)


def _inline_string_vars_into_use(text: str) -> str:
    """Rewrite `USE CATALOG {CATALOG}` -> `USE CATALOG main` using string vars
    assigned literally in the same source, so detect_use_directives can read it.
    """
    str_vars = {m.group(1): m.group(2) for m in _PY_ASSIGN_STR_RE.finditer(text)}
    if not str_vars:
        return text

    def repl(m: re.Match[str]) -> str:
        keyword, var = m.group(1), m.group(2)
        if var in str_vars:
            return f"USE {keyword} {str_vars[var]}"
        return m.group(0)

    return _USE_IN_PY_RE.sub(repl, text)


def _qualify_ast_results(parse: dict[str, Any], default_catalog, default_schema) -> None:
    """Promote bare table names emitted by the PySpark AST parser to 3-part FQNs.

    The AST parser records `saveAsTable("gold_x")` / `spark.table("silver_y")`
    verbatim; without qualification the stored node-ids (`col:gold_x::c`) never
    match the trace API's fully-qualified lookup (`col:cat.sch.gold_x::c`). No-op
    when no defaults were detected or names are already qualified.
    """
    if not default_catalog:
        return
    q = lambda n: _qualify_fqn(n, default_catalog=default_catalog, default_schema=default_schema)
    if parse.get("output_table_fqn"):
        parse["output_table_fqn"] = q(parse["output_table_fqn"])
    for m in parse.get("column_mappings") or []:
        if m.get("output_table_fqn"):
            m["output_table_fqn"] = q(m["output_table_fqn"])
        if m.get("source_fqn"):
            m["source_fqn"] = q(m["source_fqn"])
    if parse.get("table_references"):
        parse["table_references"] = [q(t) for t in parse["table_references"]]


def parse_artifact_cells(normalized_cells_json: str, *, artifact_id: str) -> dict[str, Any]:
    cells = json.loads(normalized_cells_json)
    if not isinstance(cells, list):
        cells = []

    merged: dict[str, Any] = {
        "artifact_id": artifact_id,
        "language": "mixed",
        "statements_parsed": 0,
        "statements_skipped": 0,
        "column_mappings": [],
        "table_references": [],
        "output_table_fqn": None,
        "warnings": [],
    }

    sql_chunks: list[str] = []
    py_cells: list[dict[str, Any]] = []

    for c in cells:
        lang = str(c.get("language") or "").lower()
        src = str(c.get("source") or "")
        if lang in ("sql", "sql cell", "dbc_language_sql"):
            sql_chunks.append(src)
        elif lang in ("python", "py", "python cell", "dbc_language_python", ""):
            py_cells.append(c)

    # Notebook-wide active catalog/schema. `USE CATALOG`/`USE SCHEMA` may be set
    # in SQL cells OR from Python via `spark.sql(f"USE CATALOG {CATALOG}")`, and
    # applies to every later cell (incl. bare `saveAsTable("t")`). Scan both up
    # front so bare names get qualified consistently across SQL and PySpark.
    _all_src = "\n".join(sql_chunks) + "\n" + "\n".join(
        str(c.get("source") or "") for c in py_cells
    )
    default_catalog, default_schema = detect_use_directives(
        _inline_string_vars_into_use(_all_src)
    )

    if sql_chunks:
        for chunk in sql_chunks:
            p = parse_sql_text(
                chunk,
                artifact_id=artifact_id,
                default_catalog=default_catalog,
                default_schema=default_schema,
            )
            merged["statements_parsed"] += p["statements_parsed"]
            merged["statements_skipped"] += p["statements_skipped"]
            merged["column_mappings"].extend(p["column_mappings"])
            merged["table_references"].extend(p["table_references"])
            merged["warnings"].extend(p["warnings"])
            if merged["output_table_fqn"] is None and p.get("output_table_fqn"):
                merged["output_table_fqn"] = p["output_table_fqn"]

    if py_cells:
        # Primary: AST-based parser (more accurate)
        p_ast = parse_pyspark_cells_ast(py_cells, artifact_id=artifact_id)
        # Qualify bare table names (saveAsTable("gold_x")) with the notebook's
        # active catalog/schema so node-ids match the trace API's FQN lookup.
        _qualify_ast_results(p_ast, default_catalog, default_schema)
        merged["statements_parsed"] += p_ast["statements_parsed"]
        merged["statements_skipped"] += p_ast["statements_skipped"]
        merged["column_mappings"].extend(p_ast["column_mappings"])
        merged["table_references"].extend(p_ast["table_references"])
        merged["warnings"].extend(p_ast["warnings"])
        if merged["output_table_fqn"] is None and p_ast.get("output_table_fqn"):
            merged["output_table_fqn"] = p_ast["output_table_fqn"]

        # Fallback: regex parser ONLY if AST produced nothing useful.
        # This prevents duplicate column_mappings when both parsers
        # extract the same withColumn/select patterns.
        ast_has_results = (
            p_ast["column_mappings"]
            or p_ast["table_references"]
            or p_ast.get("output_table_fqn")
        )
        if not ast_has_results:
            p_rx = parse_pyspark_cells(py_cells, artifact_id=artifact_id)
            _qualify_ast_results(p_rx, default_catalog, default_schema)
            merged["column_mappings"].extend(p_rx["column_mappings"])
            merged["table_references"].extend(p_rx["table_references"])
            merged["warnings"].extend(p_rx["warnings"])
            if merged["output_table_fqn"] is None and p_rx.get("output_table_fqn"):
                merged["output_table_fqn"] = p_rx["output_table_fqn"]

    merged["table_references"] = sorted(set(merged["table_references"]))
    return merged
