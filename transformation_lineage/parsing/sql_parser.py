"""Best-effort SQL parsing for column-level hints (sqlparse-based, extensible)."""

from __future__ import annotations

import logging
import re
from typing import Any

import sqlparse

logger = logging.getLogger(__name__)


# Three-part `catalog.schema.table` OR two-part `schema.table`. Two-part
# names are common when the notebook starts with `USE CATALOG <x>`; the
# parse functions then use `default_catalog` to fully qualify them.
_FQN_3 = r"`?[\w]+`?\.`?[\w]+`?\.`?[\w]+`?"
_FQN_2 = r"`?[\w]+`?\.`?[\w]+`?"
_FQN = rf"(?:{_FQN_3}|{_FQN_2})"

_TABLE_RE = re.compile(
    rf"\b(?:from|join)\s+({_FQN})",
    re.IGNORECASE,
)

_ALIAS_RE = re.compile(
    rf"\b(?:from|join)\s+({_FQN})(?:\s+(?:as\s+)?([`\"]?[\w]+[`\"]?))?",
    re.IGNORECASE,
)

_OUTPUT_TABLE_RES = [
    re.compile(rf"\binsert\s+(?:into|overwrite)\s+(?:table\s+)?({_FQN})", re.IGNORECASE),
    re.compile(
        rf"\bcreate\s+(?:or\s+replace\s+)?table\s+(?:if\s+not\s+exists\s+)?({_FQN})",
        re.IGNORECASE,
    ),
    # Declarative / Lakeflow targets: MATERIALIZED VIEW, (STREAMING) [LIVE] TABLE,
    # and plain VIEW — supporting `OR REPLACE`/`OR REFRESH` and `TEMPORARY`.
    # Without these, MV / streaming-table / DLT statements parse to no output
    # target and yield zero transformation edges (silent empty popup).
    re.compile(
        rf"\bcreate\s+(?:or\s+(?:replace|refresh)\s+)?(?:temporary\s+)?"
        rf"(?:materialized\s+view|streaming\s+(?:live\s+)?table|live\s+table|view)\s+"
        rf"(?:if\s+not\s+exists\s+)?({_FQN})",
        re.IGNORECASE,
    ),
    re.compile(rf"\bmerge\s+into\s+({_FQN})", re.IGNORECASE),
]

# `USE CATALOG <name>` / `USE SCHEMA <name>` (also `USE DATABASE <name>`).
USE_CATALOG_RE = re.compile(r"\buse\s+catalog\s+`?([\w]+)`?", re.IGNORECASE)
USE_SCHEMA_RE = re.compile(r"\buse\s+(?:schema|database)\s+`?([\w]+)`?", re.IGNORECASE)


def _strip_quotes(s: str) -> str:
    return s.replace("`", "").replace('"', "").strip()


def _qualify_fqn(
    name: str,
    *,
    default_catalog: str | None,
    default_schema: str | None,
) -> str:
    """Promote a 1- or 2-part identifier to a 3-part FQN using the supplied
    defaults. Already-qualified names pass through unchanged.
    """
    if not name:
        return name
    parts = name.split(".")
    if len(parts) == 3:
        return name
    if len(parts) == 2 and default_catalog:
        return f"{default_catalog}.{name}"
    if len(parts) == 1 and default_catalog and default_schema:
        return f"{default_catalog}.{default_schema}.{name}"
    return name


# `STREAM(<table>)` / `STREAM (<table>)` — the streaming-read wrapper used by
# streaming tables and DLT. Source-table extraction must see the inner table,
# not the literal "STREAM"; this rewrites the wrapper away before FROM/JOIN
# matching. (Subquery forms like STREAM(SELECT ...) don't match the simple
# identifier group and are left for normal nested parsing.)
_STREAM_WRAP_RE = re.compile(r"\bstream\s*\(\s*([`\"\w.]+)\s*\)", re.IGNORECASE)


def _unwrap_streaming_sources(sql: str) -> str:
    return _STREAM_WRAP_RE.sub(r"\1", sql)


# SQL keywords / type names to exclude when guessing bare column identifiers
# from an expression (used ONLY to resolve unqualified columns against a single
# known source table, e.g. streaming tables / CTAS without table aliases).
_SQL_NONCOLUMN = {
    "as", "and", "or", "not", "is", "null", "case", "when", "then", "else", "end",
    "distinct", "over", "partition", "by", "order", "asc", "desc", "interval", "cast",
    "try_cast", "date", "timestamp", "int", "integer", "bigint", "smallint", "tinyint",
    "string", "varchar", "char", "double", "float", "decimal", "numeric", "boolean",
    "true", "false", "from", "where", "group", "having", "select", "on", "join", "using",
    "in", "like", "between", "current_date", "current_timestamp", "stream", "div", "ilike",
}


def _unqualified_columns(expr: str) -> list[str]:
    """Best-effort bare column identifiers in an expression: function names
    (identifier immediately followed by ``(``), SQL keywords, type names, and
    string literals are excluded. Used only to attribute unqualified columns to
    a SINGLE known source table when no alias-qualified references are present.
    """
    e = re.sub(r"'[^']*'", " ", expr)
    e = re.sub(r'"[^"]*"', " ", e)
    out: list[str] = []
    for m in re.finditer(r"\b([a-zA-Z_]\w*)\b(\s*\()?", e):
        name, is_func = m.group(1), m.group(2)
        if is_func or name.lower() in _SQL_NONCOLUMN:
            continue
        if name not in out:
            out.append(name)
    return out


def extract_tables(
    sql: str,
    *,
    default_catalog: str | None = None,
    default_schema: str | None = None,
) -> list[str]:
    """Rough table list from FROM / JOIN clauses, qualified to 3-part names
    using `default_catalog` / `default_schema` when the source SQL only
    uses 2- or 1-part identifiers.
    """
    sql = _unwrap_streaming_sources(sql)
    tables: list[str] = []
    for m in _TABLE_RE.finditer(sql):
        raw = _strip_quotes(m.group(1))
        tables.append(
            _qualify_fqn(raw, default_catalog=default_catalog, default_schema=default_schema)
        )
    return sorted(set(tables))


def extract_alias_map(
    sql: str,
    *,
    default_catalog: str | None = None,
    default_schema: str | None = None,
) -> dict[str, str]:
    """
    Map alias (and unqualified table name) -> fully-qualified table name.

    Covers:
      FROM catalog.schema.table AS a  -> {"a": "catalog.schema.table", "table": "catalog.schema.table"}
      FROM catalog.schema.table t     -> {"t": "catalog.schema.table", "table": "catalog.schema.table"}
      FROM catalog.schema.table       -> {"table": "catalog.schema.table"}
      FROM schema.table         (with `USE CATALOG c`) -> {"table": "c.schema.table"}
    """
    mapping: dict[str, str] = {}
    sql = _unwrap_streaming_sources(sql)
    for match in _ALIAS_RE.finditer(sql):
        raw = _strip_quotes(match.group(1))
        fqn = _qualify_fqn(raw, default_catalog=default_catalog, default_schema=default_schema)
        short = fqn.split(".")[-1].lower()
        mapping.setdefault(short, fqn)
        alias = match.group(2)
        if alias:
            a = _strip_quotes(alias).lower()
            mapping[a] = fqn
    return mapping


def extract_output_table(
    sql: str,
    *,
    default_catalog: str | None = None,
    default_schema: str | None = None,
) -> str | None:
    """Detect the write target (INSERT INTO / CREATE TABLE AS / MERGE INTO)."""
    for r in _OUTPUT_TABLE_RES:
        m = r.search(sql)
        if m:
            raw = _strip_quotes(m.group(1))
            return _qualify_fqn(
                raw, default_catalog=default_catalog, default_schema=default_schema
            )
    return None


def extract_select_aliases(sql: str) -> list[dict[str, Any]]:
    """
    Map SELECT output aliases to raw expression text (best-effort).

    Returns rows: {"output_column": str, "expr": str}
    """
    out: list[dict[str, Any]] = []
    try:
        stmts = sqlparse.parse(sql)
    except Exception as e:
        logger.debug("sqlparse failed: %s", e)
        return out
    for stmt in stmts:
        out.extend(_aliases_from_one_stmt(stmt))
    return out


def _aliases_from_one_stmt(stmt: Any) -> list[dict[str, Any]]:
    """Extract SELECT aliases from a single sqlparse Statement.

    Walks **every** ``SELECT ... FROM ...`` segment, not just the first. CTAS
    statements with a `WITH` clause (`CREATE TABLE ... AS WITH cte AS
    (SELECT ... FROM ...) SELECT outer FROM cte`) have at least two SELECTs;
    capturing only the first misses the table's actual output columns
    defined by the outer SELECT. Capturing all of them is over-eager but
    safe -- the column names line up because outer SELECTs reference the
    same names the inner CTEs alias to, and graph_builder dedupes.
    """
    out: list[dict[str, Any]] = []
    flat = str(stmt)
    for m in re.finditer(
        r"select\s+(.*?)\s+from\s+", flat, flags=re.IGNORECASE | re.DOTALL
    ):
        select_list = m.group(1)
        for part in _split_select_list(select_list):
            part = part.strip()
            if not part:
                continue
            alias_m = re.search(r"(?is)\bas\s+([\w]+)\s*$", part)
            if alias_m:
                out.append(
                    {
                        "output_column": alias_m.group(1),
                        "expr": part[: alias_m.start()].strip(),
                    }
                )
                continue
            tokens = part.split()
            if len(tokens) >= 2 and tokens[-2].lower() != "as":
                out.append(
                    {"output_column": tokens[-1], "expr": " ".join(tokens[:-1])}
                )
                continue
            # Bare reference like `w.warehouse_id` (no AS): use the
            # column name as the output column and the qualified
            # reference as the expression so source attribution still
            # works. Plain `column_name` (single token, no dot) also
            # falls into this branch as a passthrough.
            bare = part.strip()
            if "." in bare:
                qualifier, _, col = bare.rpartition(".")
                # Tolerate trailing whitespace / commas defensively.
                col = col.strip().split()[0] if col.strip() else ""
                if col and col.replace("_", "").isalnum():
                    out.append({"output_column": col, "expr": bare})
            elif bare and bare.replace("_", "").isalnum() and not bare.startswith("'"):
                out.append({"output_column": bare, "expr": bare})
    return out


def _split_select_list(select_list: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in select_list:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def _extract_ctes(stmt_sql: str) -> tuple[list[tuple[str, str]], str]:
    """Split a statement into its CTEs and main query.

    For ``CREATE ... AS WITH a AS (q1), b AS (q2) <main>`` returns
    ``([("a", q1), ("b", q2)], "<main>")``. Returns ``([], stmt_sql)`` when there
    is no CTE block. Uses balanced-paren scanning so nested parens inside a CTE
    body don't terminate it early.
    """
    m = re.search(r"\bwith\b", stmt_sql, re.IGNORECASE)
    if not m:
        return [], stmt_sql
    rest = stmt_sql[m.end():]
    ctes: list[tuple[str, str]] = []
    while True:
        nm = re.match(r"\s*([`\"]?[\w]+[`\"]?)\s+as\s*\(", rest, re.IGNORECASE)
        if not nm:
            break
        name = _strip_quotes(nm.group(1))
        open_idx = nm.end() - 1  # position of '('
        depth = 0
        close_idx = -1
        for j in range(open_idx, len(rest)):
            c = rest[j]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    close_idx = j
                    break
        if close_idx == -1:
            break
        ctes.append((name, rest[open_idx + 1:close_idx]))
        rest = rest[close_idx + 1:]
        cm = re.match(r"\s*,", rest)
        if cm:
            rest = rest[cm.end():]
            continue
        break
    if not ctes:
        return [], stmt_sql
    return ctes, rest  # rest = the main query after the CTE block


def _cte_column_map(
    body: str,
    *,
    default_catalog: str | None,
    default_schema: str | None,
) -> dict[str, tuple[str | None, str, str]]:
    """Map a CTE's output columns -> (source_fqn, source_column, expr).

    Resolves each output the same way the main parser does, but SCOPED to the
    CTE's own FROM/JOIN tables, so ``sum(lifetime_revenue) AS total_revenue``
    inside ``FROM customer_lifetime_value`` attributes to that one table.
    """
    tables = extract_tables(body, default_catalog=default_catalog, default_schema=default_schema)
    alias_map = extract_alias_map(body, default_catalog=default_catalog, default_schema=default_schema)
    out: dict[str, tuple[str | None, str, str]] = {}
    try:
        stmts = sqlparse.parse(body)
    except Exception:  # noqa: BLE001
        stmts = []
    aliases: list[dict[str, Any]] = []
    for s in stmts:
        aliases.extend(_aliases_from_one_stmt(s))
    single_src = tables[0] if len(tables) == 1 else None
    for a in aliases:
        col = a["output_column"]
        expr = a["expr"]
        qual = re.findall(r"\b([\w]+)\.([\w]+)\b", expr)
        if qual:
            ta, sc = qual[0]
            out[col] = (alias_map.get(ta.lower()), sc, expr)
        else:
            ucols = _unqualified_columns(expr) if single_src else []
            if single_src and ucols:
                out[col] = (single_src, ucols[0], expr)
            else:
                out[col] = (None, col, expr)
    return out


def parse_sql_text(
    sql: str,
    *,
    artifact_id: str,
    default_catalog: str | None = None,
    default_schema: str | None = None,
) -> dict[str, Any]:
    """Parse a SQL chunk into column-mapping records.

    Each statement (CREATE TABLE AS SELECT, INSERT ... SELECT, MERGE, plain
    SELECT) is processed independently so multiple writes concatenated in
    the same chunk each get correctly attributed to their own target table.
    Without this, a notebook cell that ingests ten bronze tables in
    sequence collapses every column mapping onto whichever target appears
    first in the cell.

    `default_catalog` and `default_schema` come from `USE CATALOG`/`USE SCHEMA`
    directives elsewhere in the notebook (passed in by the caller after
    scanning the joined notebook SQL); they're used to promote 1- or 2-part
    identifiers to fully-qualified 3-part FQNs.
    """
    try:
        stmts = sqlparse.parse(sql)
    except Exception as e:
        logger.debug("sqlparse failed: %s", e)
        stmts = []

    all_tables: list[str] = []
    all_mappings: list[dict[str, Any]] = []
    artifact_output_fqn: str | None = None
    statements_parsed = 0
    statements_total = 0

    for stmt in stmts:
        stmt_sql = str(stmt)
        if not stmt_sql.strip():
            continue
        statements_total += 1

        stmt_output = extract_output_table(
            stmt_sql, default_catalog=default_catalog, default_schema=default_schema
        )
        stmt_tables = extract_tables(
            stmt_sql, default_catalog=default_catalog, default_schema=default_schema
        )
        stmt_alias_map = extract_alias_map(
            stmt_sql, default_catalog=default_catalog, default_schema=default_schema
        )
        stmt_aliases = _aliases_from_one_stmt(stmt)

        all_tables.extend(stmt_tables)
        if artifact_output_fqn is None and stmt_output:
            artifact_output_fqn = stmt_output

        # ── CTE-aware path ──────────────────────────────────────────
        # `WITH cte AS (...) SELECT cte.col ...` — the outer SELECT references CTE
        # aliases, not base tables. Resolve each CTE's columns to their base source
        # (scoped to the CTE's own FROM), then map the main query's `cte.col`
        # references through that. Without this, CTE refs resolve to nothing
        # (phantom unresolved-external edges) and bare CTE columns can't attribute
        # because the statement pools every CTE's tables.
        ctes, main_sql = _extract_ctes(stmt_sql)
        if ctes:
            cte_maps = {
                name.lower(): _cte_column_map(
                    body, default_catalog=default_catalog, default_schema=default_schema
                )
                for name, body in ctes
            }
            main_alias_map = extract_alias_map(
                main_sql, default_catalog=default_catalog, default_schema=default_schema
            )
            main_aliases: list[dict[str, Any]] = []
            try:
                for s in sqlparse.parse(main_sql):
                    main_aliases.extend(_aliases_from_one_stmt(s))
            except Exception:  # noqa: BLE001
                main_aliases = []
            for a in main_aliases:
                out_col = a["output_column"]
                expr = a["expr"]
                for ta, col in re.findall(r"\b([\w]+)\.([\w]+)\b", expr):
                    cte = cte_maps.get(ta.lower())
                    if cte is not None:
                        src_fqn, src_col, inner_expr = cte.get(col, (None, col, expr))
                        all_mappings.append({
                            "artifact_id": artifact_id, "output_column": out_col,
                            "output_table_fqn": stmt_output,
                            "source_ref": src_col, "source_fqn": src_fqn,
                            "source_column": src_col,
                            "expr": (inner_expr or expr)[:2000], "expr_lang": "sql",
                        })
                    else:
                        all_mappings.append({
                            "artifact_id": artifact_id, "output_column": out_col,
                            "output_table_fqn": stmt_output,
                            "source_ref": f"{ta}.{col}",
                            "source_fqn": main_alias_map.get(ta.lower()),
                            "source_column": col,
                            "expr": expr[:2000], "expr_lang": "sql",
                        })
            if main_aliases or stmt_tables or stmt_output:
                statements_parsed += 1
            continue

        for a in stmt_aliases:
            src_cols = re.findall(r"\b([\w]+)\.([\w]+)\b", a["expr"])
            if not src_cols:
                # No alias-qualified columns. If there's exactly ONE source table
                # (common for streaming tables / simple CTAS without aliases),
                # attribute the expression's bare column identifiers to that
                # single source so real edges form. Otherwise fall back to an
                # unresolved mapping so the output column is at least visible.
                single_src = stmt_tables[0] if len(stmt_tables) == 1 else None
                ucols = _unqualified_columns(a["expr"]) if single_src else []
                if single_src and ucols:
                    for col in ucols:
                        all_mappings.append(
                            {
                                "artifact_id": artifact_id,
                                "output_column": a["output_column"],
                                "output_table_fqn": stmt_output,
                                "source_ref": col,
                                "source_fqn": single_src,
                                "source_column": col,
                                "expr": a["expr"][:2000],
                                "expr_lang": "sql",
                            }
                        )
                else:
                    all_mappings.append(
                        {
                            "artifact_id": artifact_id,
                            "output_column": a["output_column"],
                            "output_table_fqn": stmt_output,
                            "source_ref": "",
                            "source_fqn": None,
                            "source_column": "",
                            "expr": a["expr"][:2000],
                            "expr_lang": "sql",
                        }
                    )
                continue
            for tbl_alias, col in src_cols:
                resolved_fqn = stmt_alias_map.get(tbl_alias.lower())
                all_mappings.append(
                    {
                        "artifact_id": artifact_id,
                        "output_column": a["output_column"],
                        "output_table_fqn": stmt_output,
                        "source_ref": f"{tbl_alias}.{col}",
                        "source_fqn": resolved_fqn,
                        "source_column": col,
                        "expr": a["expr"][:2000],
                        "expr_lang": "sql",
                    }
                )

        if stmt_aliases or stmt_tables or stmt_output:
            statements_parsed += 1

    return {
        "artifact_id": artifact_id,
        "language": "sql",
        "statements_parsed": statements_parsed,
        "statements_skipped": max(0, statements_total - statements_parsed),
        "column_mappings": all_mappings,
        "table_references": sorted(set(all_tables)),
        "output_table_fqn": artifact_output_fqn,
        "warnings": [],
    }


def detect_use_directives(sql: str) -> tuple[str | None, str | None]:
    """Return (catalog, schema) seen in the most recent USE statements,
    or (None, None) if none are present.
    """
    cat_m = USE_CATALOG_RE.search(sql)
    schema_m = USE_SCHEMA_RE.search(sql)
    return (
        cat_m.group(1) if cat_m else None,
        schema_m.group(1) if schema_m else None,
    )
