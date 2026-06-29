"""Build column-level graph records from parse dictionaries."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from transformation_lineage.types import LineageEdgeRecord, LineageNodeRecord


def _eid(*parts: str) -> str:
    h = hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
    return h


def _col_node_id(table_fqn: str | None, column: str, *, artifact_id: str, fallback_tag: str) -> str:
    """Canonical column ID when FQN is known; artifact-scoped fallback otherwise."""
    if table_fqn and column:
        return f"col:{table_fqn}::{column}"
    return f"col:{fallback_tag}:{artifact_id}:{column or 'unknown'}"


# Priority-ordered pattern → category. First match wins.
_CATEGORY_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bover\s*\(", re.IGNORECASE), "window"),
    (re.compile(r"\.over\(\s*Window", re.IGNORECASE), "window"),
    (
        re.compile(
            r"\b(?:sum|avg|mean|count|min|max|stddev(?:_pop|_samp)?|variance|var_pop|var_samp|"
            r"collect_list|collect_set|first|last|approx_count_distinct|percentile(?:_approx)?)\s*\(",
            re.IGNORECASE,
        ),
        "aggregation",
    ),
    (re.compile(r"\bcase\s+when\b", re.IGNORECASE), "case"),
    (re.compile(r"\b(?:iff|if|when)\s*\(", re.IGNORECASE), "case"),
    (re.compile(r"\b(?:coalesce|ifnull|nvl|nullif)\s*\(", re.IGNORECASE), "null_handling"),
    (re.compile(r"\bcast\s*\(", re.IGNORECASE), "cast"),
    (re.compile(r"::\s*[\w()]+", re.IGNORECASE), "cast"),
    (re.compile(r"\.cast\(", re.IGNORECASE), "cast"),
    (
        re.compile(
            r"\b(?:concat(?:_ws)?|substr(?:ing)?|upper|lower|trim|ltrim|rtrim|"
            r"replace|regexp_replace|regexp_extract|split|lpad|rpad|initcap|length)\s*\(",
            re.IGNORECASE,
        ),
        "string_fn",
    ),
    (
        re.compile(
            r"\b(?:date_trunc|date_add|date_sub|datediff|to_date|to_timestamp|"
            r"current_date|current_timestamp|year|month|dayofmonth|dayofweek|hour|minute|second|"
            r"unix_timestamp|from_unixtime)\s*\(",
            re.IGNORECASE,
        ),
        "date_fn",
    ),
    (re.compile(r"[+\-*/%]"), "arithmetic"),
]


def _classify_transform(expr: str) -> str:
    """Return a short category tag for a transformation expression."""
    if not expr:
        return "unknown"
    stripped = expr.strip()
    # Pure column reference (e.g. `o.amount`, `col("amount")`, `amount`)
    if re.fullmatch(r"[\w.\"'` ]+", stripped) or re.fullmatch(
        r"(?:col|F\.col)\(\s*[\"'][\w]+[\"']\s*\)", stripped, re.IGNORECASE
    ):
        return "projection"
    for pat, cat in _CATEGORY_PATTERNS:
        if pat.search(expr):
            return cat
    return "other"


def build_graph_from_parse_results(
    parse: dict[str, Any],
    *,
    default_table_fqn: str | None = None,
) -> tuple[list[LineageNodeRecord], list[LineageEdgeRecord]]:
    """
    Construct nodes/edges for a single artifact parse.

    Column node IDs are canonical `col:{catalog.schema.table}::{column}` when
    the table FQN is known (parser-detected INSERT INTO / saveAsTable / CTAS, or
    the `default_table_fqn` override), with artifact-scoped fallback IDs
    otherwise.

    Each derive edge (and the produced column node) carries a
    `transform_category` tag (aggregation, window, case, arithmetic, cast,
    string_fn, date_fn, null_handling, projection, other) computed from the
    expression text. The full expression is stored on the column node's
    `meta_json.expr` without truncation.
    """
    artifact_id = str(parse["artifact_id"])
    nodes: dict[str, LineageNodeRecord] = {}
    edges: list[LineageEdgeRecord] = []

    xfm_id = f"xfm:{artifact_id}"
    nodes[xfm_id] = LineageNodeRecord(
        node_id=xfm_id,
        node_type="transformation",
        label=f"artifact {artifact_id}",
        table_fqn=None,
        column_name=None,
        artifact_id=artifact_id,
        meta_json=json.dumps({"language": parse.get("language")}),
    )

    for tbl in parse.get("table_references") or []:
        tid = f"tbl:{tbl}"
        if tid not in nodes:
            nodes[tid] = LineageNodeRecord(
                node_id=tid,
                node_type="table",
                label=tbl,
                table_fqn=tbl,
                column_name=None,
                artifact_id=artifact_id,
                meta_json="{}",
            )
        eid = _eid("read", artifact_id, tbl)
        edges.append(
            LineageEdgeRecord(
                edge_id=f"e_{eid}",
                src_id=tid,
                dst_id=xfm_id,
                edge_type="read",
                artifact_id=artifact_id,
                meta_json="{}",
            )
        )

    artifact_output_fqn = parse.get("output_table_fqn") or default_table_fqn

    for m in parse.get("column_mappings") or []:
        out = m.get("output_column") or "unknown"
        src_ref = m.get("source_ref") or ""
        src_fqn = m.get("source_fqn")
        src_col = m.get("source_column") or ""
        expr = m.get("expr") or ""
        expr_lang = m.get("expr_lang") or "unknown"
        category = _classify_transform(expr)
        # Per-mapping override (set by AST parser per write-sink) wins over the
        # artifact-level fallback so multi-output notebooks attribute each
        # column to its real target table.
        output_table_fqn = m.get("output_table_fqn") or artifact_output_fqn

        out_nid = _col_node_id(output_table_fqn, out, artifact_id=artifact_id, fallback_tag="out")
        if out_nid not in nodes:
            nodes[out_nid] = LineageNodeRecord(
                node_id=out_nid,
                node_type="column",
                label=out,
                table_fqn=output_table_fqn,
                column_name=out,
                artifact_id=artifact_id,
                meta_json=json.dumps(
                    {"expr": expr, "transform_category": category, "expr_lang": expr_lang}
                ),
            )

        src_nid: str | None = None
        if src_col:
            src_nid = _col_node_id(src_fqn, src_col, artifact_id=artifact_id, fallback_tag="unresolved")
            # Self-loop guard: a column can't be derived from itself. This happens
            # when an alias mis-resolves to the output table (e.g. a stale parse
            # attributing `oe.col` to the target instead of the FROM source).
            # Such an edge renders as a target node with no upstream and, sharing
            # the same edge_id as the real edge, shadows it in the BFS dedup. Skip
            # the mapping entirely so the genuine source edge survives.
            if src_nid == out_nid:
                continue
            if src_nid not in nodes:
                nodes[src_nid] = LineageNodeRecord(
                    node_id=src_nid,
                    node_type="column",
                    label=src_col,
                    table_fqn=src_fqn,
                    column_name=src_col,
                    artifact_id=artifact_id,
                    meta_json=json.dumps({"source_ref": src_ref}) if src_ref else "{}",
                )
            eid_r = _eid("col_read", artifact_id, src_nid)
            edges.append(
                LineageEdgeRecord(
                    edge_id=f"e_{eid_r}",
                    src_id=src_nid,
                    dst_id=xfm_id,
                    edge_type="read",
                    artifact_id=artifact_id,
                    meta_json="{}",
                )
            )

        # Include src_node_id (and the resolved source col) in the edge_id so two
        # outputs deriving from the same-named column of DIFFERENT source tables
        # don't collapse to one edge.
        eid_d = _eid("derive", artifact_id, out, src_ref, src_nid or "")
        edges.append(
            LineageEdgeRecord(
                edge_id=f"e_{eid_d}",
                src_id=xfm_id,
                dst_id=out_nid,
                edge_type="derive",
                artifact_id=artifact_id,
                meta_json=json.dumps(
                    {
                        "source_ref": src_ref,
                        # The EXACT resolved source column node. The whole artifact
                        # shares one xfm node, so the endpoints builder must pin the
                        # source by node id — matching on column name alone cross-
                        # joins an output to every source table that happens to have
                        # a column of the same name.
                        "src_node_id": src_nid,
                        "transform_category": category,
                        "expr": expr,
                        "expr_lang": expr_lang,
                    }
                ),
            )
        )

    return list(nodes.values()), edges
