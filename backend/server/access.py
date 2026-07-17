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
import logging
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
ACCESS_LOOKBACK_DAYS = int(os.environ.get("ACCESS_LOOKBACK_DAYS", "90"))


def _execute_sql(sql: str) -> list[dict]:
    if not WAREHOUSE_ID:
        raise RuntimeError("No SQL warehouse available. Set DATABRICKS_WAREHOUSE_ID.")
    client = _get_client()
    resp = client.statement_execution.execute_statement(
        statement=sql, warehouse_id=WAREHOUSE_ID, wait_timeout=SQL_WAIT_TIMEOUT,
    )
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
    """Return declared UC grants on `catalog.schema.table` via SHOW GRANTS."""
    full_name = f"{catalog}.{schema}.{table}"
    try:
        rows = _execute_sql(f"SHOW GRANTS ON TABLE `{catalog}`.`{schema}`.`{table}`")
        return [
            {
                "principal": r.get("Principal") or r.get("principal"),
                "privilege": r.get("ActionType") or r.get("privilege"),
                "object_type": r.get("ObjectType") or "TABLE",
                "granted_by": r.get("GrantedBy") or r.get("granted_by"),
            }
            for r in rows
        ]
    except Exception as e:
        logger.info(f"access: SHOW GRANTS failed for {full_name}: {e}")
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
        rows = _execute_sql(
            f"SELECT "
            f"  user_identity.email AS user_email, "
            f"  action_name, "
            f"  COUNT(*) AS access_count, "
            f"  MAX(event_time) AS last_accessed_at "
            f"FROM system.access.audit "
            f"WHERE request_params.full_name_arg = '{full_name}' "
            f"  AND event_time >= dateadd(DAY, -{ACCESS_LOOKBACK_DAYS}, current_timestamp()) "
            f"  AND action_name IN ('getTable','updateTable','createTable','deleteTable','renameTable','truncateTable') "
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
# Combined view
# ---------------------------------------------------------------------------

def get_access_summary(catalog: str, schema: str, table: str) -> dict:
    """Return both declared grants and empirical access in one payload.

    Also computes a `dormant_grants` list — principals who have SELECT but
    haven't appeared in the audit log for ACCESS_LOOKBACK_DAYS days.
    """
    grants = get_declared_grants(catalog, schema, table)
    audit = get_audit_access(catalog, schema, table)

    audit_users = {r["user_email"] for r in audit if r.get("user_email")}
    select_grantees = {
        r["principal"] for r in grants
        if r.get("privilege", "").upper() in ("SELECT", "ALL PRIVILEGES")
        and r.get("principal")
    }
    dormant = sorted(select_grantees - audit_users)

    return {
        "table_full_name": f"{catalog}.{schema}.{table}",
        "lookback_days": ACCESS_LOOKBACK_DAYS,
        "declared_grants": grants,
        "audit_access": audit,
        "dormant_grants": dormant,
        "unique_empirical_users": len(audit_users),
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
