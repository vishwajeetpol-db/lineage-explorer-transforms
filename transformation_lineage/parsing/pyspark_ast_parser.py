"""AST-based PySpark parser for column-level lineage.

Replaces the regex-based heuristic parser for any cell that parses as valid
Python. Walks `ast` to identify:

  1. String constants:        CATALOG = "main"
  2. F-string assignments:    full_table = f"{CATALOG}.silver.demand"
  3. DataFrame -> table:      df_pos = spark.table(f"{CATALOG}.bronze.pos_sales")
  4. DataFrame -> chain:      df_x = df_pos.filter(...).join(...).agg(...)
  5. Write sinks:             df_x.write.format(...).mode(...).saveAsTable(t)

For each write sink, the chain bound to the source DataFrame is walked and
column-shaping ops (`.agg`, `.select`, `.withColumn`, `.withColumnRenamed`,
plus aliases inside `.groupBy`) are turned into column_mappings with the
same shape the regex parser emits.

Source-column attribution: if the chain reads a single base table, every
unattributed source column is assigned that FQN (matching the regex
parser's `single_source_fqn` heuristic). Multi-source chains leave
`source_fqn` NULL — graph_builder still creates the derive edge.
"""

from __future__ import annotations

import ast
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# Methods that reshape columns; their outputs become new column nodes
_OUTPUT_METHODS = {"agg", "select", "withColumn", "withColumnRenamed", "groupBy"}

# Methods that don't change the column set but extend the source DataFrame set
_PASSTHROUGH_METHODS = {
    "filter", "where", "distinct", "dropDuplicates", "drop",
    "repartition", "coalesce", "cache", "persist",
    "orderBy", "sort", "limit", "sample", "unionByName", "union",
    "alias",  # DataFrame alias for join, not column alias
}


@dataclass
class _SymbolTable:
    string_vars: dict[str, str] = field(default_factory=dict)
    df_to_table: dict[str, str] = field(default_factory=dict)
    df_chains: dict[str, ast.AST] = field(default_factory=dict)
    # DataFrame alias -> base table FQN, e.g. `other.alias("o")` => {"o": "...other"}.
    # Lets alias-qualified column refs (`F.col("o.sku_id")`) resolve to a source table.
    df_alias_map: dict[str, str] = field(default_factory=dict)


def _resolve_string(node: ast.AST | None, st: _SymbolTable) -> str | None:
    """Render a string-valued AST node to a literal. Returns None if not resolvable."""
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return st.string_vars.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for v in node.values:
            if isinstance(v, ast.Constant) and isinstance(v.value, str):
                parts.append(v.value)
            elif isinstance(v, ast.FormattedValue):
                inner = _resolve_string(v.value, st)
                if inner is None:
                    return None
                parts.append(inner)
            else:
                return None
        return "".join(parts)
    return None


def _unparse(node: ast.AST) -> str:
    """Render an AST node back to Python source text."""
    try:
        return ast.unparse(node)
    except Exception:
        return ""


def _is_attr_call(node: ast.AST, attr: str) -> bool:
    """True iff node is `<obj>.<attr>(...)`."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attr
    )


def _is_spark_table(node: ast.AST) -> bool:
    """True iff node is `spark.table(...)` or `spark.read.table(...)`."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != "table":
        return False
    inner = node.func.value
    if isinstance(inner, ast.Name) and inner.id == "spark":
        return True
    if (
        isinstance(inner, ast.Attribute)
        and inner.attr == "read"
        and isinstance(inner.value, ast.Name)
        and inner.value.id == "spark"
    ):
        return True
    return False


def _strip_passthrough_chain(node: ast.AST) -> ast.AST:
    """Skip `.write.format(...).mode(...).option(...)` to expose `.saveAsTable`'s receiver."""
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        if cur.func.attr in {"format", "mode", "option", "options", "partitionBy", "bucketBy", "sortBy"}:
            cur = cur.func.value
        else:
            break
    return cur


def _resolve_writer_to_df(writer_node: ast.AST) -> ast.AST | None:
    """Given a `.saveAsTable`'s receiver, walk back to the underlying DataFrame node."""
    cur = _strip_passthrough_chain(writer_node)
    # Now cur is `<df>.write` or similar
    if isinstance(cur, ast.Attribute) and cur.attr == "write":
        return cur.value
    return None


def _collect_symbols(tree: ast.AST, st: _SymbolTable) -> None:
    """Pass 1: walk top-level Assigns, populating string_vars / df_to_table / df_chains."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        name = node.targets[0].id
        rhs = node.value

        # 1. String constants and f-strings
        s = _resolve_string(rhs, st)
        if s is not None:
            st.string_vars[name] = s
            continue

        # 2. spark.table / spark.read.table -> DataFrame bound to a table FQN
        if _is_spark_table(rhs):
            assert isinstance(rhs, ast.Call)
            arg = rhs.args[0] if rhs.args else None
            fqn = _resolve_string(arg, st)
            if fqn:
                st.df_to_table[name] = fqn
            continue

        # 3. Anything else assigned from a Call expression -> potential chain
        if isinstance(rhs, (ast.Call, ast.Attribute, ast.Name)):
            st.df_chains[name] = rhs


def _collect_df_aliases(tree: ast.AST, st: _SymbolTable) -> None:
    """Pass 1b: record DataFrame aliases (`<df>.alias("o")`) -> base table FQN.

    Distinguishes DataFrame aliases from column aliases: a DataFrame receiver
    traces to exactly one base-table FQN, whereas a column expression like
    `F.col("x").alias("y")` traces to none and is ignored here. Must run after
    `_collect_symbols` so df_to_table / df_chains are populated.
    """
    for node in ast.walk(tree):
        pair = _extract_alias_pair(node)
        if not pair:
            continue
        name, inner = pair
        roots = _trace_root_fqns(inner, st)
        if len(set(roots)) == 1:
            st.df_alias_map[name] = roots[0]


def _resolve_qualified_col(col: str, df_alias_map: dict[str, str]) -> tuple[str, str | None]:
    """Split a possibly alias-qualified column ref.

    `o.sku_id` with a known DataFrame alias `o` -> ("sku_id", "<fqn of o>").
    A bare column or an unknown prefix (e.g. a struct field) -> (col, None).
    """
    if "." in col:
        prefix, base = col.split(".", 1)
        if prefix in df_alias_map:
            return base, df_alias_map[prefix]
    return col, None


def _flatten_chain(node: ast.AST) -> list[ast.Call]:
    """Linearize `df.a().b().c()` -> [a_call, b_call, c_call] in execution order."""
    chain: list[ast.Call] = []
    cur = node
    while isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
        chain.append(cur)
        cur = cur.func.value
    chain.reverse()
    return chain


def _trace_root_fqns(node: ast.AST, st: _SymbolTable, seen: set[str] | None = None) -> list[str]:
    """Find every base-table FQN feeding into `node`, recursing through chain bindings."""
    seen = seen or set()
    fqns: list[str] = []

    def visit(cur: ast.AST) -> None:
        if isinstance(cur, ast.Call) and _is_spark_table(cur):
            arg = cur.args[0] if cur.args else None
            fqn = _resolve_string(arg, st)
            if fqn:
                fqns.append(fqn)
            return
        if isinstance(cur, ast.Name):
            if cur.id in st.df_to_table:
                fqns.append(st.df_to_table[cur.id])
                return
            if cur.id in st.df_chains and cur.id not in seen:
                seen.add(cur.id)
                visit(st.df_chains[cur.id])
            return
        if isinstance(cur, ast.Call) and isinstance(cur.func, ast.Attribute):
            visit(cur.func.value)
            for a in cur.args:
                visit(a)

    visit(node)
    # Dedupe, preserve order
    return list(dict.fromkeys(fqns))


def _find_col_refs(node: ast.AST) -> list[str]:
    """Collect column names referenced under `node` via F.col("x"), col("x"), or string literals
    passed to `F.<fn>("col")`-style aggregate calls. Heuristic; intentionally permissive."""
    refs: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            # F.col("x") or col("x")
            fn_attr = (
                sub.func.attr if isinstance(sub.func, ast.Attribute)
                else sub.func.id if isinstance(sub.func, ast.Name)
                else None
            )
            if fn_attr == "col" and sub.args:
                if isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                    refs.append(sub.args[0].value)
            elif (
                isinstance(sub.func, ast.Attribute)
                and isinstance(sub.func.value, ast.Name)
                and sub.func.value.id in {"F", "f", "functions"}
            ):
                # F.sum("x"), F.countDistinct("x"), etc. — string args are usually columns.
                for a in sub.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        # Filter obvious non-column literals (single chars, format strings, paths)
                        v = a.value
                        if v and v.replace("_", "").replace(".", "").isalnum() and not v.isdigit():
                            refs.append(v)
    return list(dict.fromkeys(refs))


def _extract_alias_pair(node: ast.AST) -> tuple[str, ast.AST] | None:
    """If `node` is `<expr>.alias("name")`, return ("name", <expr>). Else None."""
    if isinstance(node, ast.Call) and _is_attr_call(node, "alias"):
        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            return node.args[0].value, node.func.value  # type: ignore[union-attr]
    return None


def _outputs_from_call_args(call: ast.Call) -> list[tuple[str, ast.AST, list[str]]]:
    """For `.agg(...)`, `.select(...)`, `.groupBy(...)` extract (output_col, expr_node, source_cols)."""
    out: list[tuple[str, ast.AST, list[str]]] = []
    for arg in call.args:
        alias = _extract_alias_pair(arg)
        if alias:
            name, expr_node = alias
            out.append((name, expr_node, _find_col_refs(expr_node)))
            continue
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            # Plain "col_name" pass-through. Spark names the output by the last
            # segment, so `o.sku_id` -> output `sku_id`; keep the qualified ref
            # as the source so the alias can resolve to a table.
            out.append((arg.value.split(".")[-1], arg, [arg.value]))
            continue
        # F.col("x") without .alias() — output column name = source column's last
        # segment (drops any `alias.`/`struct.` qualifier, matching Spark).
        if (
            isinstance(arg, ast.Call)
            and (
                (isinstance(arg.func, ast.Attribute) and arg.func.attr == "col")
                or (isinstance(arg.func, ast.Name) and arg.func.id == "col")
            )
            and arg.args
            and isinstance(arg.args[0], ast.Constant)
            and isinstance(arg.args[0].value, str)
        ):
            n = arg.args[0].value
            out.append((n.split(".")[-1], arg, [n]))
    # Keyword args (e.g. `.agg(out_col=F.sum("x"))`) — uncommon but cheap to handle
    for kw in call.keywords or []:
        if kw.arg:
            out.append((kw.arg, kw.value, _find_col_refs(kw.value)))
    return out


def _collect_call_arg_col_refs(call: ast.Call) -> list[str]:
    """Column refs appearing inside the args/kwargs of `call` (not its receiver)."""
    refs: list[str] = []
    for arg in call.args:
        refs.extend(_find_col_refs(arg))
    for kw in call.keywords or []:
        if kw.value is not None:
            refs.extend(_find_col_refs(kw.value))
    return list(dict.fromkeys(refs))


def _record_alias(
    alias_map: dict[str, dict[str, Any]],
    col_first_seen: dict[str, str],
    name: str,
    expr_node: ast.AST | None,
    src_cols: list[str],
    available_fqns: list[str],
    df_alias_map: dict[str, str] | None = None,
) -> None:
    """Resolve `src_cols` through df_alias_map / alias_map / col_first_seen, then
    record the new alias `name` with its resolved sources and the most likely FQN.
    """
    df_alias_map = df_alias_map or {}
    resolved_cols: list[str] = []
    resolved_fqn: str | None = None
    for sc in src_cols:
        base_col, alias_fqn = _resolve_qualified_col(sc, df_alias_map)
        if alias_fqn is not None:
            # Alias-qualified table column (e.g. `o.sku_id`) -> direct source.
            resolved_cols.append(base_col)
            if resolved_fqn is None:
                resolved_fqn = alias_fqn
        elif base_col in alias_map:
            resolved_cols.extend(alias_map[base_col]["source_cols"])
            if resolved_fqn is None:
                resolved_fqn = alias_map[base_col]["source_fqn"]
        else:
            resolved_cols.append(base_col)
            if resolved_fqn is None and base_col in col_first_seen:
                resolved_fqn = col_first_seen[base_col]

    if resolved_fqn is None:
        unique = list(dict.fromkeys(available_fqns))
        if len(unique) == 1:
            resolved_fqn = unique[0]

    alias_map[name] = {
        "source_cols": resolved_cols or [name],
        "source_fqn": resolved_fqn,
        "expr": _unparse(expr_node) if expr_node is not None else None,
    }


def _populate_resolution(
    df_node: ast.AST,
    st: _SymbolTable,
    alias_map: dict[str, dict[str, Any]],
    col_first_seen: dict[str, str],
    visited: set[str],
) -> None:
    """Walk `df_node` and every chain reachable from it, populating the
    artifact-scoped `alias_map` (column -> resolved source) and
    `col_first_seen` (column -> table where it was first referenced).

    `visited` tracks chain names to prevent cycles.
    """
    if isinstance(df_node, ast.Name):
        if df_node.id in visited:
            return
        visited.add(df_node.id)
        if df_node.id in st.df_chains:
            _populate_resolution(
                st.df_chains[df_node.id], st, alias_map, col_first_seen, visited
            )
        # df_to_table entries don't introduce aliases; their FQN is reachable
        # via _trace_root_fqns when we ask for it.
        return

    if not isinstance(df_node, ast.Call):
        return
    if _is_spark_table(df_node):
        return

    chain = _flatten_chain(df_node)
    if not chain:
        return

    available_fqns: list[str] = []

    leftmost = chain[0].func.value
    _populate_resolution(leftmost, st, alias_map, col_first_seen, visited)
    available_fqns.extend(_trace_root_fqns(leftmost, st))

    for call in chain:
        if not isinstance(call.func, ast.Attribute):
            continue
        method = call.func.attr

        # First-mention attribution: a column referenced for the first time
        # in a chain with exactly one source FQN is attributed to that FQN,
        # even if the chain later joins additional tables.
        for col in _collect_call_arg_col_refs(call):
            if col in col_first_seen or col in alias_map:
                continue
            unique = list(dict.fromkeys(available_fqns))
            if len(unique) == 1:
                col_first_seen[col] = unique[0]

        if method == "join" and call.args:
            joined = call.args[0]
            _populate_resolution(joined, st, alias_map, col_first_seen, visited)
            available_fqns = available_fqns + _trace_root_fqns(joined, st)

        if method in {"select", "agg", "groupBy"}:
            for name, expr_node, src_cols in _outputs_from_call_args(call):
                _record_alias(
                    alias_map, col_first_seen, name, expr_node, src_cols, available_fqns,
                    st.df_alias_map,
                )
        elif method == "withColumn":
            if (
                len(call.args) >= 2
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
            ):
                name = call.args[0].value
                expr_node = call.args[1]
                src_cols = _find_col_refs(expr_node)
                _record_alias(
                    alias_map, col_first_seen, name, expr_node, src_cols, available_fqns,
                    st.df_alias_map,
                )
        elif method == "withColumnRenamed":
            if (
                len(call.args) >= 2
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
                and isinstance(call.args[1], ast.Constant)
                and isinstance(call.args[1].value, str)
            ):
                old = call.args[0].value
                new = call.args[1].value
                if old in alias_map:
                    alias_map[new] = dict(alias_map[old])
                else:
                    src_fqn = col_first_seen.get(old)
                    if src_fqn is None:
                        unique = list(dict.fromkeys(available_fqns))
                        if len(unique) == 1:
                            src_fqn = unique[0]
                    alias_map[new] = {
                        "source_cols": [old],
                        "source_fqn": src_fqn,
                        "expr": f'col("{old}")',
                    }


def _build_resolution_state(
    df_node: ast.AST, st: _SymbolTable
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Build (alias_map, col_first_seen) for chains reachable from df_node.

    Scope is per-saveAsTable: aliases from chains rooted at *other* UC tables
    are not pulled in, so a column name reused across cells (e.g. cell 6 and
    cell 11 both creating a local `warehouse_id`) is resolved against the
    chain that actually feeds the current write target.
    """
    alias_map: dict[str, dict[str, Any]] = {}
    col_first_seen: dict[str, str] = {}
    _populate_resolution(df_node, st, alias_map, col_first_seen, set())
    return alias_map, col_first_seen


def _walk_chain_for_outputs(
    df_node: ast.AST,
    st: _SymbolTable,
    artifact_id: str,
    output_table_fqn: str | None,
    *,
    alias_map: dict[str, dict[str, Any]] | None = None,
    col_first_seen: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Walk a chain originating at `df_node` and emit column mappings."""
    mappings: list[dict[str, Any]] = []
    chain = _flatten_chain(df_node)
    source_fqns = _trace_root_fqns(df_node, st)
    single_source_fqn = source_fqns[0] if len(set(source_fqns)) == 1 else None
    alias_map = alias_map or {}
    col_first_seen = col_first_seen or {}

    for call in chain:
        if not isinstance(call.func, ast.Attribute):
            continue
        method = call.func.attr

        if method == "withColumn":
            # .withColumn("name", expr)
            if len(call.args) >= 2 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str):
                name = call.args[0].value
                expr_node = call.args[1]
                expr_text = _unparse(call)
                src_cols = _find_col_refs(expr_node)
                _emit_mappings(
                    mappings, artifact_id, name, expr_text, src_cols,
                    single_source_fqn, alias_map, col_first_seen, st.df_alias_map,
                )

        elif method == "withColumnRenamed":
            # .withColumnRenamed("old", "new")
            if (
                len(call.args) >= 2
                and isinstance(call.args[0], ast.Constant)
                and isinstance(call.args[0].value, str)
                and isinstance(call.args[1], ast.Constant)
                and isinstance(call.args[1].value, str)
            ):
                old = call.args[0].value
                new = call.args[1].value
                _emit_mappings(
                    mappings, artifact_id, new, f'col("{old}")', [old],
                    single_source_fqn, alias_map, col_first_seen, st.df_alias_map,
                )

        elif method in {"select", "agg", "groupBy"}:
            for name, expr_node, src_cols in _outputs_from_call_args(call):
                expr_text = _unparse(expr_node)
                _emit_mappings(
                    mappings, artifact_id, name, expr_text, src_cols,
                    single_source_fqn, alias_map, col_first_seen, st.df_alias_map,
                )

        # Other methods (filter/join/etc.) don't create new output columns at this level.

    return mappings, source_fqns


def _emit_mappings(
    mappings: list[dict[str, Any]],
    artifact_id: str,
    output_column: str,
    expr: str,
    source_cols: list[str],
    source_fqn: str | None,
    alias_map: dict[str, dict[str, Any]] | None = None,
    col_first_seen: dict[str, str] | None = None,
    df_alias_map: dict[str, str] | None = None,
) -> None:
    """Emit one mapping per source column.

    For each `sc` in `source_cols`, resolution priority is:
        1. `alias_map[sc]`     -- `sc` is itself an alias defined upstream;
                                  substitute its underlying source columns and
                                  use its resolved FQN.
        2. `col_first_seen[sc]` -- `sc` is a real base column; attribute it to
                                  the table where it was first referenced.
        3. `source_fqn`        -- chain-level fallback (single root FQN).
    """
    alias_map = alias_map or {}
    col_first_seen = col_first_seen or {}
    df_alias_map = df_alias_map or {}

    if not source_cols:
        mappings.append(
            {
                "artifact_id": artifact_id,
                "output_column": output_column,
                "source_ref": "",
                "source_fqn": None,
                "source_column": "",
                "expr": (expr or "")[:2000],
                "expr_lang": "pyspark",
            }
        )
        return

    for sc in source_cols:
        base_col, alias_fqn = _resolve_qualified_col(sc, df_alias_map)
        if alias_fqn is not None:
            # Alias-qualified table column (e.g. `o.sku_id`): resolve the prefix to
            # its table and emit the bare column name (not `o.sku_id`).
            mappings.append(
                {
                    "artifact_id": artifact_id,
                    "output_column": output_column,
                    "source_ref": base_col,
                    "source_fqn": alias_fqn,
                    "source_column": base_col,
                    "expr": (expr or "")[:2000],
                    "expr_lang": "pyspark",
                }
            )
        elif base_col in alias_map:
            entry = alias_map[base_col]
            resolved_fqn = entry["source_fqn"]
            if resolved_fqn is None:
                resolved_fqn = col_first_seen.get(base_col, source_fqn)
            for rsc in (entry["source_cols"] or [base_col]):
                mappings.append(
                    {
                        "artifact_id": artifact_id,
                        "output_column": output_column,
                        "source_ref": rsc,
                        "source_fqn": resolved_fqn,
                        "source_column": rsc,
                        "expr": (expr or "")[:2000],
                        "expr_lang": "pyspark",
                    }
                )
        else:
            resolved_fqn = col_first_seen.get(base_col, source_fqn)
            mappings.append(
                {
                    "artifact_id": artifact_id,
                    "output_column": output_column,
                    "source_ref": base_col,
                    "source_fqn": resolved_fqn,
                    "source_column": base_col,
                    "expr": (expr or "")[:2000],
                    "expr_lang": "pyspark",
                }
            )


def _find_save_targets(tree: ast.AST, st: _SymbolTable) -> list[tuple[ast.AST, str]]:
    """Find `<chain>.write[...].saveAsTable(<arg>)` calls. Return (df_node, target_fqn)."""
    out: list[tuple[ast.AST, str]] = []
    for node in ast.walk(tree):
        if not _is_attr_call(node, "saveAsTable"):
            continue
        assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        target = _resolve_string(node.args[0] if node.args else None, st)
        if not target:
            continue
        df_node = _resolve_writer_to_df(node.func.value)
        if df_node is None:
            continue
        out.append((df_node, target))
    return out


def parse_pyspark_ast(
    text: str,
    *,
    artifact_id: str,
    symbol_table: _SymbolTable | None = None,
) -> dict[str, Any]:
    """Parse a single Python source string. Returns the standard parse-result dict
    or raises SyntaxError so the caller can fall back to the regex parser."""
    tree = ast.parse(text)
    st = symbol_table or _SymbolTable()
    _collect_symbols(tree, st)
    _collect_df_aliases(tree, st)

    mappings: list[dict[str, Any]] = []
    table_refs: list[str] = list(st.df_to_table.values())
    output_table_fqn: str | None = None

    # Find every saveAsTable sink and walk its chain
    for df_node, target_fqn in _find_save_targets(tree, st):
        if output_table_fqn is None:
            output_table_fqn = target_fqn

        # Resolve df_node to its underlying chain expression
        chain_expr = df_node
        if isinstance(df_node, ast.Name) and df_node.id in st.df_chains:
            chain_expr = st.df_chains[df_node.id]

        # Build per-target resolution state by walking every reachable chain.
        # Aliases are scoped to this target so a column name reused across
        # cells doesn't bleed in from a chain that doesn't feed this write.
        alias_map, col_first_seen = _build_resolution_state(chain_expr, st)

        chain_mappings, chain_sources = _walk_chain_for_outputs(
            chain_expr, st, artifact_id, target_fqn,
            alias_map=alias_map, col_first_seen=col_first_seen,
        )
        # Stamp this sink's target FQN onto every mapping it produced so
        # graph_builder attributes them correctly per-chain (not artifact-wide).
        for m in chain_mappings:
            m["output_table_fqn"] = target_fqn
        mappings.extend(chain_mappings)
        table_refs.extend(chain_sources)

    statements_parsed = 1 if (mappings or table_refs) else 0
    return {
        "artifact_id": artifact_id,
        "language": "python",
        "statements_parsed": statements_parsed,
        "statements_skipped": 1 - statements_parsed,
        "column_mappings": mappings,
        "table_references": sorted(set(table_refs)),
        "output_table_fqn": output_table_fqn,
        "warnings": [],
    }


def parse_pyspark_cells_ast(
    cells: list[dict[str, Any]], *, artifact_id: str
) -> dict[str, Any]:
    """Parse a list of normalized cells with the AST extractor.

    Each cell is parsed against its OWN symbol table. Cross-cell constants
    (e.g. ``CATALOG = "main"`` defined in cell 1 and read by cell 14) are
    still resolvable via a shared base layer, but only when a variable's
    binding is consistent across every cell that assigns it. Variables that
    are reassigned to different values across cells (the classic
    ``full_table = f"...table_a"`` / ``full_table = f"...table_b"`` pattern)
    are NOT promoted to the shared layer — each cell's own binding wins.
    """
    # Pass 1: parse every parseable cell and collect its own symbol table.
    cell_tables: list[tuple[str, _SymbolTable]] = []
    for c in cells:
        lang = str(c.get("language", "")).lower()
        if lang not in ("python", "py", "python cell", "dbc_language_python", ""):
            continue
        src = str(c.get("source") or "")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        st = _SymbolTable()
        _collect_symbols(tree, st)
        cell_tables.append((src, st))

    # Pass 2: derive shared base layer = variables whose value is identical
    # in every cell that binds them. Volatile names (different value per
    # cell) are excluded so the per-cell binding is the only thing visible
    # when that cell is processed.
    shared_strings = _shared_bindings(t.string_vars for _, t in cell_tables)
    shared_df_to_table = _shared_bindings(t.df_to_table for _, t in cell_tables)
    # df_chains hold AST nodes; comparing them across cells is unreliable
    # and rarely valuable (chains live within a cell). Keep per-cell only.

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

    for src, _ in cell_tables:
        # Fresh per-cell table seeded with shared constants. parse_pyspark_ast
        # re-runs _collect_symbols on this cell's AST, which overlays (and
        # overrides) any volatile names with the cell-local binding.
        cell_st = _SymbolTable(
            string_vars=dict(shared_strings),
            df_to_table=dict(shared_df_to_table),
            df_chains={},
        )
        try:
            part = parse_pyspark_ast(src, artifact_id=artifact_id, symbol_table=cell_st)
        except SyntaxError as e:
            merged["warnings"].append(f"ast SyntaxError: {e}")
            merged["statements_skipped"] += 1
            continue
        merged["statements_parsed"] += part["statements_parsed"]
        merged["statements_skipped"] += part["statements_skipped"]
        merged["column_mappings"].extend(part["column_mappings"])
        merged["table_references"].extend(part["table_references"])
        merged["warnings"].extend(part["warnings"])
        if merged["output_table_fqn"] is None and part.get("output_table_fqn"):
            merged["output_table_fqn"] = part["output_table_fqn"]

    merged["table_references"] = sorted(set(merged["table_references"]))
    return merged


def _shared_bindings(per_cell_dicts) -> dict[str, str]:
    """Return name -> value for variables whose value is identical in every
    cell that assigns them. A name with conflicting values across cells is
    omitted so it only resolves via the per-cell binding.
    """
    values: dict[str, set[str]] = {}
    for d in per_cell_dicts:
        for k, v in d.items():
            values.setdefault(k, set()).add(v)
    return {k: next(iter(vs)) for k, vs in values.items() if len(vs) == 1}
