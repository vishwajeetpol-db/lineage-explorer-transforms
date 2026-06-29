"""Normalize Databricks notebook exports and plain SQL/Python files into parseable cells."""

from __future__ import annotations

import json
import re
from typing import Any


_MAGIC = re.compile(r"^\s*%\s*\w+.*$", re.MULTILINE)

# Databricks source-format notebooks separate cells with `# COMMAND ----------`.
# Anchored to start-of-line so it doesn't accidentally match content inside
# string literals.
_DBX_CELL_SEPARATOR = re.compile(r"^\s*#\s*COMMAND\s*-+\s*$", re.MULTILINE)

# In Databricks source-format notebooks, non-default-language cells are
# encoded as `# MAGIC %sql` (or %python/%scala/%r/%md) followed by lines
# prefixed with `# MAGIC `. Detect the language and strip the prefix so the
# downstream parser sees raw SQL/Python instead of a wall of comments.
_DBX_MAGIC_LANG = re.compile(r"^\s*#\s*MAGIC\s+%(\w+)\b", re.MULTILINE)


def strip_magic_blocks(source: str) -> str:
    """Remove line magics like %sql / %python (best-effort; keeps cell body)."""
    return "\n".join(line for line in source.splitlines() if not _MAGIC.match(line))


def _decode_dbx_magic_cell(cell_text: str, default_lang: str) -> tuple[str, str]:
    """Detect a cell's language from its `# MAGIC %lang` directive (if any)
    and strip the `# MAGIC ` prefix from every line so the actual code body
    is what the parser sees. Also drops `# DBTITLE` annotations which the
    SQL parser would treat as syntax errors.

    Returns (language, cleaned_source). When no magic directive is present
    the cell body is returned unchanged with `default_lang`.
    """
    m = _DBX_MAGIC_LANG.search(cell_text)
    if not m:
        return default_lang, cell_text
    lang = m.group(1).lower()
    out_lines: list[str] = []
    for line in cell_text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("# MAGIC %"):
            # The %lang declaration itself is metadata, not code.
            continue
        if stripped.startswith("# DBTITLE"):
            continue
        if stripped.startswith("# MAGIC"):
            # Remove the `# MAGIC` prefix (and the single space after it,
            # if present) to recover the underlying SQL/Python line.
            content = line[line.index("# MAGIC") + len("# MAGIC"):]
            if content.startswith(" "):
                content = content[1:]
            out_lines.append(content)
        else:
            out_lines.append(line)
    return lang, "\n".join(out_lines)


def split_dbx_source_cells(raw: str) -> list[str] | None:
    """Split a Databricks source-format notebook into cell bodies.

    Returns the list of per-cell sources if the notebook uses the
    ``# COMMAND ----------`` separator format, else None so the caller can
    fall back to treating ``raw`` as a single cell.
    """
    if not _DBX_CELL_SEPARATOR.search(raw):
        return None
    return [chunk for chunk in _DBX_CELL_SEPARATOR.split(raw) if chunk.strip()]


def try_parse_dbc_notebook_json(raw: str) -> list[dict[str, Any]] | None:
    """
    Databricks workspace export for notebooks is often JSON with cells.

    If parsing fails, returns None so caller treats `raw` as a plain file.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    cells = data.get("cells")
    if not isinstance(cells, list):
        return None
    out: list[dict[str, Any]] = []
    for c in cells:
        if not isinstance(c, dict):
            continue
        lang = c.get("language") or c.get("cell_type")
        src = c.get("source")
        if isinstance(src, list):
            text = "".join(src)
        elif isinstance(src, str):
            text = src
        else:
            text = ""
        out.append({"language": str(lang or ""), "source": strip_magic_blocks(text)})
    return out if out else None


def normalize_to_cells(raw: str, default_language: str) -> list[dict[str, Any]]:
    parsed = try_parse_dbc_notebook_json(raw)
    if parsed:
        return parsed
    lang = default_language or "python"
    chunks = split_dbx_source_cells(raw)
    if chunks is not None:
        cells: list[dict[str, Any]] = []
        for c in chunks:
            cell_lang, cell_src = _decode_dbx_magic_cell(c, lang)
            # Drop pure markdown / scala / r cells — they have no lineage value
            # and would be misclassified by the python or sql parsers.
            if cell_lang in ("md", "markdown", "scala", "r"):
                continue
            cells.append({"language": cell_lang, "source": strip_magic_blocks(cell_src)})
        return cells
    return [{"language": lang, "source": strip_magic_blocks(raw)}]


def cells_to_json(cells: list[dict[str, Any]]) -> str:
    return json.dumps(cells, ensure_ascii=False)
