"""Pure-stdlib parser for Spark's Analyzed Logical Plan text.

Vendored unchanged from lineage-plan-capture (src/lineage_capture/plan_parser.py).
Turns the plan text captured by `capture.analyzed_plan(df)` into per-column
{target_column, source_columns, expression, confidence, notes} dicts — the
same shape the static SQL/PySpark parser in transformation_lineage/parsing/
already produces, so downstream consumers (backend/plan_capture_service.py)
can treat either source uniformly.
"""
from __future__ import annotations
import json
import re
import sys

# Attribute reference: `colName#123` or `colName#123L`
ATTR_RE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?\b")

_NODE_RE = re.compile(r"^(\+?-*'?)([A-Za-z][A-Za-z0-9]*)\b(.*)$")


def clean_nodes(plan_text: str):
    """[(indent:int, body:str), ...] — one entry per plan-tree line, comments/
    blank lines dropped. `indent` is the raw leading-symbol column (used only to
    walk top-to-bottom; the parser does not depend on exact tree depth)."""
    nodes = []
    for raw in plan_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        stripped = line.lstrip(" ")
        indent = len(line) - len(stripped)
        nodes.append((indent, stripped))
    return nodes


def parse_schema_header(plan_text: str):
    """Column names from the trailing `root/schema` block if present, else None."""
    m = re.search(r"root\s*\n((?:\s+\|--.*\n?)+)", plan_text)
    if not m:
        return None
    names = re.findall(r"\|--\s*([A-Za-z_][A-Za-z0-9_]*)\s*:", m.group(1))
    return names or None


def split_top(bracket_body: str):
    """Split a `[...]` bracket body on top-level commas (ignores nested
    brackets/parens so `array(a, b)` doesn't get split mid-call)."""
    items, depth, cur = [], 0, []
    for ch in bracket_body:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == "," and depth == 0:
            items.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        items.append("".join(cur).strip())
    return [i for i in items if i]


def all_brackets(body: str):
    """Every top-level `[...]` bracket's inner text found in `body`, in order."""
    out, depth, start = [], 0, None
    for i, ch in enumerate(body):
        if ch == "[":
            if depth == 0:
                start = i + 1
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start is not None:
                out.append(body[start:i])
                start = None
    return out


def find_as(item: str):
    """`(expr, alias_name, expr_id)` if `item` is `<expr> AS name#id`, else None."""
    m = re.search(r"\bAS\s+([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?\s*$", item, re.IGNORECASE)
    if not m:
        return None
    expr = item[: m.start()].strip()
    return expr, m.group(1), int(m.group(2))


def build_symbol_tables(nodes):
    """(alias_def, out_name, base) — exprId -> defining expression / display name
    / base-relation full name, scanned across every node in the plan."""
    alias_def, out_name, base = {}, {}, {}
    for indent, body in nodes:
        m = re.match(r"^Relation\s+([A-Za-z0-9_.]+)", body) or re.match(r"^SubqueryAlias\s+([A-Za-z0-9_.]+)", body)
        rel_name = m.group(1) if m else None
        for b in all_brackets(body):
            for item in split_top(b):
                asx = find_as(item)
                if asx:
                    expr, name, sid = asx
                    alias_def[sid] = expr
                    out_name[sid] = name
                    if rel_name:
                        base[sid] = rel_name
                else:
                    m2 = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?", item.strip())
                    if m2 and rel_name:
                        base[int(m2.group(2))] = rel_name
    return alias_def, out_name, base


def _udf_call_names(item: str):
    return []  # placeholder retained for interface parity; see _udf_names below


def output_name(item: str):
    """The output column name an expr-list item produces, or None."""
    asx = find_as(item)
    if asx:
        return asx[1]
    m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?", item.strip())
    return m.group(1) if m else None


def root_items(nodes, schema_names=None):
    """Output expr-list of the root operator.

    Node types differ in WHERE the output list lives: Project has one bracket,
    Aggregate is `[keys], [output]`, Window is `[output], [partition], [order]`.
    Rather than special-case each, pick the bracket whose produced column names
    match the schema header; fall back to the first attribute-bearing bracket.
    """
    for indent, body in nodes:
        candidates = []
        for b in all_brackets(body):
            items = split_top(b)
            names = [n for n in (output_name(it) for it in items) if n]
            if names:
                candidates.append((items, names))
        if not candidates:
            continue
        if schema_names:
            for items, names in candidates:          # exact ordered match
                if names == schema_names:
                    return items
            for items, names in candidates:          # same set
                if set(names) == set(schema_names):
                    return items
            for items, names in candidates:          # same count
                if len(names) == len(schema_names):
                    return items
        return candidates[0][0]                       # first expr-ish bracket
    return []


# ---------- resolution ----------

def normalize_expr(expr: str) -> str:
    expr = re.sub(r"#\d+L?", "", expr)       # amount#123 -> amount; classify(x)#7 -> classify(x)
    return re.sub(r"\s+", " ", expr).strip()


def resolve_sources(expr, alias_def, out_name, base, visited=None):
    """All leaf/base source columns an expression ultimately depends on."""
    if visited is None:
        visited = set()
    sources = []
    for name, sid in ATTR_RE.findall(expr):
        sid = int(sid)
        if sid in visited:
            continue
        visited.add(sid)
        if sid in alias_def:                  # derived -> recurse
            sub = resolve_sources(alias_def[sid], alias_def, out_name, base, visited)
            sources.extend(sub)
        else:                                  # base/leaf column
            fqn = base.get(sid)
            sources.append(f"{fqn}.{name}" if fqn else name)
    # de-dupe preserving order
    seen, out = set(), []
    for s in sources:
        if s not in seen:
            seen.add(s); out.append(s)
    return out


def parse_plan(plan_text: str):
    nodes = clean_nodes(plan_text)
    schema_names = parse_schema_header(plan_text)
    alias_def, out_name, base = build_symbol_tables(nodes)
    cols = []
    for item in root_items(nodes, schema_names):
        asx = find_as(item)
        if asx:
            expr, name, sid = asx
        else:                                  # bare passthrough `name#id`
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?", item)
            if not m:
                continue
            name, sid = m.group(1), int(m.group(2))
            expr = alias_def.get(sid, item)    # follow one hop if aliased
        sources = resolve_sources(expr, alias_def, out_name, base)
        is_passthrough = bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", normalize_expr(expr)))
        udfs = _udf_names(expr)
        cols.append({
            "target_column": name,
            "source_columns": sources,
            "expression": normalize_expr(expr),
            "confidence": 0.6 if udfs else 1.0,
            "notes": (f"opaque UDF ({', '.join(udfs)}) — source cols reliable, "
                      "logic not in plan; use LLM for description"
                      if udfs else ("identity / rename" if is_passthrough else "")),
        })
    return cols


# a UDF/opaque call prints as `funcname(args)#exprId` — a ')' immediately
# followed by '#<digits>', which builtin function results never have.
_UDF_CALL_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\([^()]*(?:\([^()]*\)[^()]*)*\)#\d+")


def _udf_names(expr: str):
    names, seen = [], set()
    if not re.search(r"\)#\d+", expr) and "pythonudf" not in expr.lower():
        return names
    for m in _UDF_CALL_RE.finditer(expr):
        n = m.group(1)
        if n not in seen:
            seen.add(n); names.append(n)
    return names or ["?"]


if __name__ == "__main__":
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    print(json.dumps(parse_plan(text), indent=2))
