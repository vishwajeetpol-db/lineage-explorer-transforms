"""Pure-stdlib parser for Spark's Analyzed Logical Plan text.

Vendored unchanged from lineage-plan-capture (src/lineage_capture/plan_parser.py).
Turns the plan text captured by `capture.analyzed_plan(df)` into per-column
{target_column, source_columns, expression, confidence, notes} dicts — the
same shape the static SQL/PySpark parser in transformation_lineage/parsing/
already produces, so downstream consumers (backend/plan_capture_service.py)
can treat either source uniformly.
"""
from __future__ import annotations
import re, json, sys

ATTR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?")
LEAF_PREFIXES = ("Range", "Relation", "LogicalRelation", "HiveTableRelation",
                 "LocalRelation", "FileScan", "DataSourceV2", "JDBCRelation",
                 "LogicalRDD", "StreamingRelation", "StreamingDataSourceV2")


# ---------- depth-aware helpers ----------

def split_top(s: str, sep: str = ",") -> list[str]:
    """Split on `sep` only at bracket/paren depth 0."""
    out, depth, cur = [], 0, []
    for ch in s:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def find_as(item: str):
    """Return (expr, name, id) if `item` is `<expr> AS name#id` at depth 0, else None."""
    depth = 0
    for i in range(len(item) - 3):
        ch = item[i]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and item[i:i + 4] == " AS ":
            rhs = item[i + 4:].strip()
            m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?", rhs)
            if m:
                return item[:i].strip(), m.group(1), int(m.group(2))
    return None


def all_brackets(line: str):
    """Yield the content of every top-level [...] group in a node line."""
    depth, start = 0, None
    for i, ch in enumerate(line):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0 and start is not None:
                yield line[start + 1:i]
                start = None


def attr_bracket(line: str):
    """The first top-level [...] that holds attribute refs (`name#id`).

    Leaf nodes like `StreamingRelationV2 ..., [rowsPerSecond=1], [ts#1, val#2]`
    have an options bracket *before* the schema bracket — skip to the one with #.
    """
    for b in all_brackets(line):
        if "#" in b:
            return b
    return None


def first_bracket(line: str):
    """Return the content of the first top-level [...] in a node line, or None."""
    start = line.find("[")
    if start == -1:
        return None
    depth, i = 0, start
    while i < len(line):
        if line[i] == "[":
            depth += 1
        elif line[i] == "]":
            depth -= 1
            if depth == 0:
                return line[start + 1:i]
        i += 1
    return None


# ---------- plan structure ----------

def clean_nodes(plan_text: str):
    """Yield (indent, body) per node; drops the schema header line.

    Some leaf nodes (e.g. a Delta `StreamingRelation`/`Relation`) embed a
    multi-line `CatalogTable(...)` dump. Those continuation lines carry no tree
    connector, so we fold them back into the node they belong to — otherwise
    they look like bogus indent-0 nodes and break parent-chain tracking.
    """
    raw = [ln for ln in plan_text.splitlines() if ln.strip()]
    if raw and "[" not in raw[0] and "#" not in raw[0] and ":" in raw[0]:
        raw = raw[1:]
    merged = []
    for ln in raw:
        is_new_node = (not merged) or bool(re.search(r"(\+-|:-)", ln))
        if is_new_node:
            merged.append(ln)
        else:
            merged[-1] = merged[-1] + " " + ln.strip()
    nodes = []
    for ln in merged:
        m = re.match(r"^([ :+\-]*)(.*)$", ln)
        prefix, body = m.group(1), m.group(2)
        body = re.sub(r"^[~*]+(\(\d+\))?\s*", "", body)  # strip ~streaming / *codegen markers
        nodes.append((len(prefix), body))
    return nodes


def _table_context(body):
    """Return a fully-qualified table name if this node names one, else None.

    `Relation cat.sch.tbl[...]`            -> cat.sch.tbl
    `SubqueryAlias cat.sch.tbl`            -> cat.sch.tbl   (DLT dlt.read upstream)
    `SubqueryAlias o`                      -> None          (plain subquery alias)
    """
    m = re.match(r"SubqueryAlias\s+(`?[\w.]+`?)\s*$", body)
    if m:
        n = m.group(1).strip("`")
        return n if "." in n else None
    m = re.match(r"Relation\s+(`?[\w.]+`?)", body)
    if m:
        return m.group(1).strip("`")
    return None


def build_symbol_tables(nodes):
    """alias_def[id]=expr, out_name[id]=name, base[id]=table_fqn|None (leaf cols).

    Tracks the parent chain by indent so a leaf's base columns inherit the
    qualified table name from the nearest enclosing SubqueryAlias/Relation.
    """
    alias_def, out_name, base = {}, {}, {}
    stack = []  # (indent, body) ancestor chain
    for indent, body in nodes:
        while stack and stack[-1][0] >= indent:
            stack.pop()
        ctx = _table_context(body)
        if ctx is None:
            for anc_ind, anc_body in reversed(stack):
                t = _table_context(anc_body)
                if t:
                    ctx = t
                    break
        stack.append((indent, body))

        is_leaf = body.startswith(LEAF_PREFIXES)
        bracket = attr_bracket(body) if is_leaf else first_bracket(body)
        for name, sid in ATTR_RE.findall(body):
            out_name.setdefault(int(sid), name)
        if bracket is None:
            continue
        for item in split_top(bracket):
            asx = find_as(item)
            if asx:
                expr, name, sid = asx
                alias_def[sid] = expr
                out_name[sid] = name
            elif is_leaf:
                m = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)#(\d+)L?", item)
                if m:
                    base[int(m.group(2))] = ctx   # fqn or None
    return alias_def, out_name, base


def parse_schema_header(plan_text: str):
    """Ordered output column names from the first line (`col: type, col: type`)."""
    first = plan_text.splitlines()[0] if plan_text.strip() else ""
    if not first or "[" in first or "#" in first or ":" not in first:
        return []
    names, depth, seg = [], 0, []
    for ch in first:
        if ch in "([<":
            depth += 1
        elif ch in ")]>":
            depth -= 1
        if ch == "," and depth == 0:
            names.append("".join(seg)); seg = []
        else:
            seg.append(ch)
    if seg:
        names.append("".join(seg))
    return [s.split(":", 1)[0].strip() for s in names if s.split(":", 1)[0].strip()]


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
