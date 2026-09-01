"""Split a .sql file into individual statements, NUL-separated.

The SQL Statement Execution API accepts exactly one statement per call, so the
fixture files have to be split. Quote- and comment-aware: a `;` inside a string
literal, a quoted identifier, or a comment is not a separator.
"""
import sys

def split_statements(text: str):
    out, cur = [], []
    i, n = 0, len(text)
    quote = None  # "'", '"' or '`' when inside a literal/quoted identifier
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            cur.append(ch)
            # Backslash escape: Databricks honours \' inside a literal, so the
            # next character is data and cannot close the quote.
            if ch == "\\" and quote != "`" and nxt:
                cur.append(nxt); i += 2; continue
            if ch == quote:
                if ch != "`" and nxt == quote:   # escaped '' or "" inside a literal
                    cur.append(nxt); i += 2; continue
                quote = None
            i += 1
            continue
        if ch == "-" and nxt == "-":             # line comment
            j = text.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if ch == "/" and nxt == "*":             # block comment
            j = text.find("*/", i)
            i = n if j == -1 else j + 2
            continue
        if ch in ("'", '"', "`"):
            quote = ch; cur.append(ch); i += 1; continue
        if ch == ";":
            out.append("".join(cur)); cur = []; i += 1; continue
        cur.append(ch); i += 1
    if "".join(cur).strip():
        out.append("".join(cur))
    return [s.strip() for s in out if s.strip()]

if __name__ == "__main__":
    for stmt in split_statements(open(sys.argv[1]).read()):
        sys.stdout.write(stmt + "\0")
