"""Shared input-validation helpers for all route modules.

Import _IDENTIFIER_RE and _validate here rather than re-defining them in each
route file. This ensures a single canonical regex for UC identifier validation
(catalog, schema, table, column names) — including names that contain hyphens,
which Unity Catalog allows but an earlier copy of the regex silently rejected.

Usage:
    from backend.validators import _IDENTIFIER_RE, _validate
"""
import re
from fastapi import HTTPException

# Matches valid UC identifier characters: letters, digits, underscore, hyphen.
# Max length 255 per UC spec. Hyphen is explicitly included because UC catalog
# and schema names may contain hyphens (e.g. `my-catalog`, `adi-413`).
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}$")

# Matches a fully-qualified three-part name (catalog.schema.table)
_FULL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,255}\.[A-Za-z0-9_-]{1,255}\.[A-Za-z0-9_-]{1,255}$")


def _validate(value: str, name: str) -> str:
    """Strip, check against _IDENTIFIER_RE, raise HTTP 400 on failure."""
    v = (value or "").strip()
    if not v or not _IDENTIFIER_RE.match(v):
        raise HTTPException(status_code=400, detail=f"Invalid {name}: '{v[:50]}'")
    return v
