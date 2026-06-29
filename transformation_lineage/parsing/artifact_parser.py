"""Parse a normalized notebook artifact into merged parse dictionaries.

Python cells are parsed by the AST-based extractor first
(`parse_pyspark_cells_ast`); the regex parser is only consulted when AST
returned no mappings AND no table references, which is the natural fallback
for cells that don't compile (notebook scratch, half-finished code) or that
use idioms the AST walker doesn't yet recognize.
"""

from __future__ import annotations

import json
from typing import Any

from transformation_lineage.parsing.pyspark_ast_parser import parse_pyspark_cells_ast
from transformation_lineage.parsing.pyspark_parser import parse_pyspark_cells
from transformation_lineage.parsing.sql_parser import detect_use_directives, parse_sql_text


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

    if sql_chunks:
        # Notebook-wide `USE CATALOG`/`USE SCHEMA` is set in one cell and
        # implicitly applies to every later cell. Scan the joined SQL up
        # front so the qualifier persists across cell boundaries.
        joined_sql = "\n".join(sql_chunks)
        default_catalog, default_schema = detect_use_directives(joined_sql)

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
            merged["column_mappings"].extend(p_rx["column_mappings"])
            merged["table_references"].extend(p_rx["table_references"])
            merged["warnings"].extend(p_rx["warnings"])
            if merged["output_table_fqn"] is None and p_rx.get("output_table_fqn"):
                merged["output_table_fqn"] = p_rx["output_table_fqn"]

    merged["table_references"] = sorted(set(merged["table_references"]))
    return merged
