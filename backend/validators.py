"""Shared input-validation helpers for all route modules.

Import _IDENTIFIER_RE and _validate here rather than re-defining them in each
route file. This ensures a single canonical regex for UC identifier validation
(catalog, schema, table, column names) — including names that contain hyphens,
which Unity Catalog allows but an earlier copy of the regex silently rejected.

Usage:
    from backend.validators import _IDENTIFIER_RE, _validate
"""
import re
from urllib.parse import urlparse

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


def redact_url(url: object) -> str:
    """Reduce a URL to scheme://host for display.

    Webhook and ingest URLs routinely carry their delivery secret in the PATH
    (Slack/Teams `/services/T000/B000/XXXX`) or in the QUERY STRING
    (`?apiKey=…`), so returning one verbatim leaks a credential. Scheme+host is
    enough for an admin to recognise which endpoint a row refers to.

    Lives here rather than in a route module because more than one router returns
    stored URLs — openlineage's producer config and capability_closures' webhook
    list — and they must not drift apart on what "redacted" means.
    """
    if not url:
        return ""
    try:
        parts = urlparse(str(url))
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except Exception:
        pass
    return "(redacted)"


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


# ---------------------------------------------------------------------------
# Outbound URL safety (SSRF)
#
# The app makes exactly one outbound call to a URL it did not author: the Delta
# Sharing provider probe in federated_sync. That URL comes from Unity Catalog
# metadata (`sharing_server_url`), which anyone able to register a sharing provider
# controls — a delegated privilege in most large metastores. Unvalidated, it reaches
# the app's network position, which includes cloud instance-metadata endpoints and
# workspace-internal services that trust that position.
#
# Validation is only half the fix. The other half is a rule rather than a check:
# never attach the app's OWN credential to a request aimed at a host the app did not
# choose. See federated_sync.probe_peer.
# ---------------------------------------------------------------------------
_BLOCKED_HOST_LITERALS = {
    "localhost", "metadata", "metadata.google.internal",
    "instance-data", "169.254.169.254", "metadata.goog",
}


class UnsafeOutboundURL(ValueError):
    """The URL is not safe to fetch — private, non-HTTPS, or unresolvable."""


def assert_safe_outbound_url(url: object, what: str = "URL") -> str:
    """Return the URL if it is safe to fetch from the app, else raise.

    Rejects: non-https schemes (http included — these are cross-network calls and a
    downgrade is a plausible attack, not a convenience); credentials embedded in the
    netloc; hosts that resolve to loopback, private, link-local, reserved or
    multicast addresses; and a handful of cloud metadata names that resolve to
    routable-looking addresses on some platforms.

    Resolution matters: a public hostname can have an A record pointing at
    169.254.169.254, so checking the literal host is not enough. This still leaves a
    DNS-rebinding window between the check and the connect, which is why the
    credential rule above is the primary control and this is defence in depth.
    """
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    raw = str(url or "").strip()
    if not raw:
        raise UnsafeOutboundURL(f"{what} is empty")
    try:
        parts = urlsplit(raw)
    except Exception as e:
        raise UnsafeOutboundURL(f"{what} is not parseable: {e}") from e

    if parts.scheme.lower() != "https":
        raise UnsafeOutboundURL(
            f"{what} must use https (got {parts.scheme or 'no scheme'!r})"
        )
    if parts.username or parts.password:
        raise UnsafeOutboundURL(f"{what} must not embed credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeOutboundURL(f"{what} has no host")
    if host in _BLOCKED_HOST_LITERALS:
        raise UnsafeOutboundURL(f"{what} host {host!r} is not allowed")

    try:
        infos = socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as e:
        raise UnsafeOutboundURL(f"{what} host {host!r} does not resolve: {e}") from e

    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified
        ):
            raise UnsafeOutboundURL(
                f"{what} host {host!r} resolves to a non-public address ({ip})"
            )
    return raw
