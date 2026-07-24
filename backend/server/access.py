"""Access & security lineage — capability 20.

Combines two complementary views of who can read/write a table:

  1. *Declared grants* — SHOW GRANTS ON TABLE <fqn> run via the SDK.  These
     reflect what Unity Catalog says SHOULD be allowed.
  2. *Empirical access* — system.access.audit WHERE event_type = 'getTable' or
     similar, showing who ACTUALLY accessed the table in the configured
     lookback window.  Gaps between the two surfaces over-privileged or
     dormant grants.

All functions are non-fatal.
"""
from __future__ import annotations

import os
import time
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
# 14-day audit window by default (matches the reference tool). A wider window
# turns the audit scan into a multi-minute query that overruns the request.
ACCESS_LOOKBACK_DAYS = int(os.environ.get("ACCESS_LOOKBACK_DAYS", "14"))
# The audit scan on system.access.audit can still exceed the 50s API wait cap;
# poll past it rather than failing with "PENDING", but bound it so the /api/access
# request returns before any upstream proxy timeout.
SQL_POLL_MAX_S = int(os.environ.get("ACCESS_SQL_POLL_MAX_S", "90"))
SQL_POLL_INTERVAL_S = int(os.environ.get("SQL_POLL_INTERVAL_S", "3"))

# UC audit action names grouped for the read/write summary. Kept broad to match
# the reference tool (a table read is 'getTable'; writes span several actions).
_READ_ACTIONS = ("getTable", "generateTemporaryTableCredential", "getForeignTable")
_WRITE_ACTIONS = (
    "updateTable", "createTable", "deleteTable", "renameTable", "truncateTable",
    "updateTables", "commitTransaction", "updateMetadata",
)


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
    )
    # Poll past the 50s API wait cap (audit scans are slow) instead of failing.
    deadline = time.time() + SQL_POLL_MAX_S
    while resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        if time.time() > deadline:
            raise RuntimeError(f"SQL exceeded {SQL_POLL_MAX_S}s budget: statement_id={resp.statement_id}")
        time.sleep(SQL_POLL_INTERVAL_S)
        resp = client.statement_execution.get_statement(resp.statement_id)
    if resp.status.state != StatementState.SUCCEEDED:
        err = resp.status.error.message if resp.status.error else resp.status.state
        raise RuntimeError(f"SQL failed: {err}")
    if not resp.result or not resp.result.data_array:
        return []
    columns = [c.name for c in resp.manifest.schema.columns]
    return [dict(zip(columns, row)) for row in resp.result.data_array]


# ---------------------------------------------------------------------------
# Declared grants via SDK (SHOW GRANTS)
# ---------------------------------------------------------------------------

def get_declared_grants(catalog: str, schema: str, table: str) -> list[dict]:
    """Return declared UC grants on `catalog.schema.table`.

    Uses information_schema.table_privileges rather than `SHOW GRANTS ON TABLE`.
    SHOW GRANTS requires the caller to OWN or have MANAGE on the table, which the
    app's service principal does not — so it returned nothing. table_privileges is
    a readable view that surfaces the same grants for any principal with access to
    the catalog's information_schema.

    Rows are one privilege per (grantee) in the view; we collapse to one entry per
    principal carrying its full set of privileges (matching the reference UI).
    """
    safe_s = schema.replace("'", "")
    safe_t = table.replace("'", "")
    full_name = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(
            f"SELECT grantor, grantee, privilege_type, is_grantable "
            f"FROM `{catalog}`.information_schema.table_privileges "
            f"WHERE table_schema = '{safe_s}' AND table_name = '{safe_t}' "
            f"ORDER BY grantee, privilege_type"
        )
        by_grantee: dict[str, dict] = {}
        for r in rows:
            g = r.get("grantee")
            if not g:
                continue
            e = by_grantee.setdefault(g, {
                "principal": g,
                "privilege": r.get("privilege_type"),  # first, for back-compat callers
                "privileges": [],
                "object_type": "TABLE",
                "granted_by": r.get("grantor"),
            })
            p = r.get("privilege_type")
            if p and p not in e["privileges"]:
                e["privileges"].append(p)
        return list(by_grantee.values())
    except Exception as e:
        logger.info(f"access: table_privileges query failed for {full_name}: {e}")
        return []


# ---------------------------------------------------------------------------
# Empirical access from system.access.audit
# ---------------------------------------------------------------------------

def get_audit_access(catalog: str, schema: str, table: str) -> list[dict]:
    """Return who actually accessed the table according to system.access.audit.

    Groups by user_identity.email + action_name, returning access counts and
    last-seen timestamp.  The audit log event_type is 'getTable' for reads
    and 'updateTable' / 'createTable' for writes — we capture all of them.
    """
    full_name = f"{catalog}.{schema}.{table}"
    try:
        # Match the reference query: filter on service_name + the event_date
        # partition (fast pruning) and key on request_params['full_name_arg']
        # (bracket syntax). The prior version used dot-syntax + no service_name
        # filter and returned nothing.
        rows = _execute_sql(
            f"SELECT "
            f"  user_identity.email AS user_email, "
            f"  action_name, "
            f"  COUNT(*) AS access_count, "
            f"  MAX(event_time) AS last_accessed_at "
            f"FROM system.access.audit "
            f"WHERE service_name = 'unityCatalog' "
            f"  AND event_date >= current_date() - INTERVAL {ACCESS_LOOKBACK_DAYS} DAYS "
            f"  AND request_params['full_name_arg'] = '{full_name}' "
            f"  AND action_name IN {_READ_ACTIONS + _WRITE_ACTIONS} "
            f"GROUP BY 1, 2 "
            f"ORDER BY last_accessed_at DESC "
            f"LIMIT 200"
        )
        return [
            {
                "user_email": r.get("user_email"),
                "action_name": r.get("action_name"),
                "access_count": int(r.get("access_count") or 0),
                "last_accessed_at": str(r.get("last_accessed_at") or ""),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"access: audit query failed for {full_name}: {e}")
        return []


# ---------------------------------------------------------------------------
# Identity metadata (owner / created-by / last-altered) from information_schema
# ---------------------------------------------------------------------------

def get_table_identities(catalog: str, schema: str, table: str) -> dict:
    """Return owner / created_by / last_altered_by / last_altered_at for a table.

    Sourced from information_schema.tables. All fields best-effort — a missing
    column (older DBR) just yields None rather than raising.
    """
    full_name = f"{catalog}.{schema}.{table}"
    result = {
        "owner": None,
        "created_by": None,
        "created_at": None,
        "last_altered_by": None,
        "last_altered_at": None,
    }
    try:
        rows = _execute_sql(
            f"SELECT table_owner, created, created_by, last_altered, last_altered_by "
            f"FROM `{catalog}`.information_schema.tables "
            f"WHERE table_schema = '{schema}' AND table_name = '{table}' LIMIT 1"
        )
        if rows:
            r = rows[0]
            result["owner"] = r.get("table_owner")
            result["created_by"] = r.get("created_by")
            result["created_at"] = str(r.get("created") or "") or None
            result["last_altered_by"] = r.get("last_altered_by")
            result["last_altered_at"] = str(r.get("last_altered") or "") or None
    except Exception as e:
        logger.info(f"access: identity metadata failed for {full_name}: {e}")
    return result


def get_recent_events(catalog: str, schema: str, table: str, limit: int = 25) -> list[dict]:
    """Return the most recent individual audit events for a table (with source IP)."""
    full_name = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  event_time, "
            f"  action_name, "
            f"  user_identity.email AS user_email, "
            f"  source_ip_address "
            f"FROM system.access.audit "
            f"WHERE service_name = 'unityCatalog' "
            f"  AND event_date >= current_date() - INTERVAL {ACCESS_LOOKBACK_DAYS} DAYS "
            f"  AND request_params['full_name_arg'] = '{full_name}' "
            f"  AND action_name IN {_READ_ACTIONS + _WRITE_ACTIONS} "
            f"ORDER BY event_time DESC "
            f"LIMIT {limit}"
        )
        return [
            {
                "event_time": str(r.get("event_time") or ""),
                "action_name": r.get("action_name"),
                "user_email": r.get("user_email"),
                "source_ip": r.get("source_ip_address"),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"access: recent-events query failed for {full_name}: {e}")
        return []


# ---------------------------------------------------------------------------
# Combined view
# ---------------------------------------------------------------------------

def _scan_audit(catalog: str, schema: str, table: str, limit: int = 500) -> list[dict]:
    """One raw audit scan for a table; both the per-user summary and the recent-
    events feed are derived from this in Python, so we hit the (slow) audit table
    only ONCE per request instead of twice."""
    full_name = f"{catalog}.{schema}.{table}"
    try:
        return _execute_sql(
            f"SELECT "
            f"  CAST(event_time AS STRING) AS event_time, "
            f"  user_identity.email AS user_email, "
            f"  action_name, "
            f"  source_ip_address AS source_ip "
            f"FROM system.access.audit "
            f"WHERE service_name = 'unityCatalog' "
            f"  AND event_date >= current_date() - INTERVAL {ACCESS_LOOKBACK_DAYS} DAYS "
            f"  AND request_params['full_name_arg'] = '{full_name}' "
            f"  AND action_name IN {_READ_ACTIONS + _WRITE_ACTIONS} "
            f"ORDER BY event_time DESC "
            f"LIMIT {limit}"
        )
    except Exception as e:
        logger.info(f"access: audit scan failed for {full_name}: {e}")
        return []


def get_access_summary(catalog: str, schema: str, table: str) -> dict:
    """Return declared grants, empirical access, identity metadata, read/write
    activity counts, and recent events in one payload.

    Grants + identities come from information_schema (fast); the empirical access
    picture comes from a SINGLE audit scan whose rows feed both the per-user
    rollup and the recent-events feed.
    """
    grants = get_declared_grants(catalog, schema, table)
    identities = get_table_identities(catalog, schema, table)
    events = _scan_audit(catalog, schema, table)

    # Per-user rollup (grouped) from the raw rows.
    by_user: dict[tuple, dict] = {}
    reads = writes = 0
    for e in events:
        ue, an = e.get("user_email"), e.get("action_name")
        key = (ue, an)
        row = by_user.setdefault(key, {"user_email": ue, "action_name": an, "access_count": 0, "last_accessed_at": e.get("event_time")})
        row["access_count"] += 1
        if an in _READ_ACTIONS:
            reads += 1
        elif an in _WRITE_ACTIONS:
            writes += 1
    audit = sorted(by_user.values(), key=lambda r: r.get("last_accessed_at") or "", reverse=True)
    recent_events = events[:25]

    audit_users = {e["user_email"] for e in events if e.get("user_email")}

    def _can_read(g: dict) -> bool:
        privs = {p.upper() for p in (g.get("privileges") or [])}
        if g.get("privilege"):
            privs.add(g["privilege"].upper())
        return bool(privs & {"SELECT", "ALL PRIVILEGES", "ALL_PRIVILEGES"})
    select_grantees = {g["principal"] for g in grants if g.get("principal") and _can_read(g)}
    dormant = sorted(select_grantees - audit_users)
    grantees = {g["principal"] for g in grants if g.get("principal")}

    return {
        "table_full_name": f"{catalog}.{schema}.{table}",
        "lookback_days": ACCESS_LOOKBACK_DAYS,
        "identities": identities,
        "declared_grants": grants,
        "audit_access": audit,
        "recent_events": recent_events,
        "dormant_grants": dormant,
        "unique_empirical_users": len(audit_users),
        "grantee_count": len(grantees),
        "read_count": reads,
        "write_count": writes,
    }


def get_schema_access_summary(catalog: str, schema: str) -> list[dict]:
    """Return empirical top-N access activity across all tables in a schema."""
    try:
        rows = _execute_sql(
            f"SELECT "
            f"  request_params.full_name_arg AS table_full_name, "
            f"  COUNT(DISTINCT user_identity.email) AS unique_users, "
            f"  COUNT(*) AS total_accesses, "
            f"  MAX(event_time) AS last_access_at "
            f"FROM system.access.audit "
            f"WHERE request_params.full_name_arg LIKE '{catalog}.{schema}.%' "
            f"  AND event_time >= dateadd(DAY, -{ACCESS_LOOKBACK_DAYS}, current_timestamp()) "
            f"  AND action_name = 'getTable' "
            f"GROUP BY 1 "
            f"ORDER BY total_accesses DESC "
            f"LIMIT 100"
        )
        return rows
    except Exception as e:
        logger.info(f"access: schema-level audit query failed for {catalog}.{schema}: {e}")
        return []
