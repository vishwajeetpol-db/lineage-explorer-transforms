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


def sql_str(s: object, limit: int | None = None) -> str:
    """Escape a value for embedding in a single-quoted SQL literal.

    BACKSLASH FIRST, THEN QUOTE — the order is load-bearing. Databricks SQL
    (Spark) treats backslash as an escape character inside single-quoted literals
    by default (`spark.sql.parser.escapedStringLiterals=false`), so `\\'` is a
    literal quote that does NOT terminate the string. Quote-doubling alone is
    therefore bypassable: an attacker value starting `\\'` becomes `\\''`, whose
    first quote is consumed as an escaped quote and whose second quote CLOSES the
    literal — everything after it executes as SQL.

    Use this for every user-supplied value interpolated into SQL text. For
    identifiers (catalog/schema/table/column) prefer _validate/_IDENTIFIER_RE,
    which allow-lists instead of escaping.

    `limit` truncates AFTER escaping is accounted for, so a truncation can never
    split an escape pair and re-open the literal.
    """
    v = "" if s is None else str(s)
    if limit is not None and limit > 0:
        v = v[:limit]
    return v.replace("\\", "\\\\").replace("'", "''")


def require_admin(request) -> str:
    """Raise 403 unless the caller is an app admin; returns the caller's email.

    The app has no auth middleware and no router-level dependencies, so every
    privileged handler must call this explicitly. Import and call it rather than
    re-implementing the check, so a new endpoint can't silently ship ungated.
    """
    from backend.main import _get_user_info
    email, is_admin = _get_user_info(request)
    if not is_admin:
        raise HTTPException(status_code=403, detail="Admin required")
    return email or ""
