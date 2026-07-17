"""Feature Flags & Control Panel engine.

Delta-backed, admin-gated toggles for optional, higher-cost/higher-risk
capabilities: Runtime Plan Capture (lineage_tracking), Captured-Plan
Precedence (column_transformation), and Cross-Workspace Sync (federated_sync).

Every flag defaults to DISABLED. A flag's *effective* state is the AND of:
  - the persisted Delta row (user-visible toggle, admin-settable, survives
    redeploys/restarts — unlike the in-process cache)
  - a hard env-var kill switch (ops-level; defaults to allowing the DB value)

This guarantees the "dormant unless explicitly enabled" contract the plugin
architecture requires: nothing in backend/plan_capture_service.py or
backend/federated_sync.py runs its gated logic unless get_flag_state()
returns True for that specific flag.
"""
from __future__ import annotations

import os
import logging
import threading
from typing import Optional

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
FLAGS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.feature_flags"
FLAGS_AUDIT_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.feature_flags_audit"
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")

# Hard ops-level kill switches. Default "true" (allow) — the real on/off control
# is the per-workspace Control Panel toggle persisted in FLAGS_TABLE. Set to
# "false" to force a capability off across the whole app regardless of the DB
# flag (e.g. the plan-capture job is misbehaving and needs an instant kill
# switch that doesn't depend on the warehouse being reachable).
_ENV_KILL_SWITCH = {
    "lineage_tracking.plan_capture": os.environ.get("ENABLE_PLAN_CAPTURE", "true").lower() != "false",
    "column_transformation.captured_plan_precedence": os.environ.get(
        "ENABLE_CAPTURED_PLAN_PRECEDENCE", "true"
    ).lower() != "false",
    "federated_sync.cross_workspace": os.environ.get("ENABLE_FEDERATED_SYNC", "true").lower() != "false",
}

# ---------------------------------------------------------------------------
# Flag registry — static metadata rendered by the Control Panel. Cost/risk here
# are baseline editorial ratings (High/Medium/Low); list_flags() returns them
# as-is today — see docs/testing_plan_for_Combined_App.md for the plan to
# enrich "cost" with a live system.billing signal once plan-capture jobs exist
# to measure.
# ---------------------------------------------------------------------------
FLAG_DEFINITIONS: list[dict] = [
    {
        "id": "lineage_tracking.plan_capture",
        "module": "lineage_tracking",
        "module_label": "Lineage Tracking",
        "accent": "amber",
        "name": "Runtime Plan Capture",
        "description": (
            "Vendors the lineage-plan-capture plugin (backend/plan_capture/) into Lakeflow Jobs and "
            "Pipelines you run through this app. Right before a pipeline writes a table, it captures "
            "Spark's Analyzed Logical Plan — the exact per-column expression — into an app-owned Delta "
            "table (captured_plans / captured_cdc_specs). This is the only way to get exact "
            "transformation logic for wheel-based or dynamically-built DataFrames that the static "
            "SQL/PySpark parser can't read from source code."
        ),
        "cost": "medium",
        "risk": "low",
        "side_effects": [
            "Adds one extra Delta write (a few KB) per distinct pipeline run to the app-owned lineage schema.",
            "Adds a %pip install first-cell + one lc.capture(df, target) call to any pipeline notebook you opt in — a small, non-fatal overhead per materialized table.",
            "Capture failures are swallowed by design and never fail the underlying write, but a silently-failing capture means no error surfaces without checking the Control Panel status card.",
        ],
        "access_requirements": [
            {"privilege": "WRITE VOLUME", "scope": f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.wheels", "reason": "Stage the lineage_capture wheel so pipeline clusters can install it."},
            {"privilege": "CREATE TABLE", "scope": f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}", "reason": "The app-owned captured_plans / captured_cdc_specs tables are created on first write."},
            {"privilege": "CAN_MANAGE / CAN_MANAGE_RUN", "scope": "target Lakeflow Job or Pipeline", "reason": "Needed to add the pip-install cell and the capture() call to the pipeline notebook."},
        ],
        "depends_on": [],
    },
    {
        "id": "column_transformation.captured_plan_precedence",
        "module": "column_transformation",
        "module_label": "Column Transformation",
        "accent": "violet",
        "name": "Captured-Plan Precedence",
        "description": (
            "When a column has both a statically-parsed expression (from the transformation_lineage "
            "engine) and a runtime-captured Spark plan expression (from Runtime Plan Capture), surface "
            "the captured-plan version in the transformation drill-down as 'Runtime-captured' with its "
            "own confidence score, alongside the static-parse result — instead of relying solely on "
            "static parsing."
        ),
        "cost": "low",
        "risk": "low",
        "side_effects": [
            "Adds one extra lookup query against captured_plans per column opened in the transformation drill-down (result is cached).",
            "Has no visible effect unless Runtime Plan Capture is also enabled and has captured a plan for that specific table.",
        ],
        "access_requirements": [
            {"privilege": "SELECT", "scope": f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.captured_plans", "reason": "Read captured expressions to enrich the transformation popup."},
        ],
        "depends_on": ["lineage_tracking.plan_capture"],
    },
    {
        "id": "federated_sync.cross_workspace",
        "module": "federated_sync",
        "module_label": "Federated Sync",
        "accent": "cyan",
        "name": "Cross-Workspace Lineage Sync",
        "description": (
            "Extends the existing Delta Sharing boundary view with an explicit registry of federated "
            "peer workspaces/metastores. When enabled, the app cross-references configured peers "
            "against the Delta Sharing metadata it already reads to mark shared boundary nodes as a "
            "'known peer' instead of an anonymous share. First-cut scope: see "
            "docs/architecture.md (Known Gaps) — this does not yet implement live cross-workspace API "
            "calls or peer-initiated sync jobs, pending full extraction of FEDERATED_LINEAGE_DESIGN.docx."
        ),
        "cost": "low",
        "risk": "medium",
        "side_effects": [
            "Introduces a new app-owned federated_peers config table that an admin must populate before this changes anything visible.",
            "Does not read any row data from peer workspaces — share/recipient metadata only.",
            "A mis-registered peer alias can make the UI claim a boundary is a 'known peer' when it isn't — treat the registry as informational, not a trust boundary.",
        ],
        "access_requirements": [
            {"privilege": "SELECT", "scope": "system.information_schema (shares, recipients, providers)", "reason": "Existing Delta Sharing overlay dependency — no new grant beyond what the app already requires."},
            {"privilege": "CREATE TABLE", "scope": f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}", "reason": "The app-owned federated_peers registry is created on first write."},
            {"privilege": "Workspace Admin (recommended)", "scope": "current workspace", "reason": "Registering peers is admin-gated since it changes what boundary nodes claim to be a known peer for every user."},
        ],
        "depends_on": [],
    },
]

_flags_by_id = {f["id"]: f for f in FLAG_DEFINITIONS}


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


_ensure_lock = threading.Lock()
_ensured = False


def _ensure_tables() -> None:
    """Guarded, idempotent create — never raises. Mirrors the app-wide
    convention that ensure-table helpers degrade to empty rather than fail
    the request (see backend/transform_service.py, CLAUDE.md)."""
    global _ensured
    if _ensured:
        return
    with _ensure_lock:
        if _ensured:
            return
        try:
            _execute_sql(
                f"CREATE TABLE IF NOT EXISTS {FLAGS_TABLE} ("
                f"flag_id STRING, enabled BOOLEAN, updated_by STRING, updated_at TIMESTAMP"
                f") USING DELTA"
            )
            _execute_sql(
                f"CREATE TABLE IF NOT EXISTS {FLAGS_AUDIT_TABLE} ("
                f"flag_id STRING, enabled BOOLEAN, actor STRING, changed_at TIMESTAMP"
                f") USING DELTA"
            )
            _ensured = True
        except Exception as e:
            logger.warning(f"feature_flags: could not ensure tables (non-fatal, all flags default to disabled): {e}")


def _persisted_states() -> dict[str, bool]:
    try:
        _ensure_tables()
        rows = _execute_sql(f"SELECT flag_id, enabled FROM {FLAGS_TABLE}")
        return {r["flag_id"]: bool(r["enabled"]) for r in rows}
    except Exception as e:
        logger.info(f"feature_flags: no persisted state readable yet, defaulting all flags to disabled: {e}")
        return {}


def get_flag_state(flag_id: str) -> bool:
    """Effective on/off: persisted DB flag AND env-level kill switch. An
    unknown flag_id defaults to False — the safe default this architecture
    requires for any capability that isn't explicitly registered."""
    if flag_id not in _flags_by_id:
        return False
    if not _ENV_KILL_SWITCH.get(flag_id, True):
        return False
    return _persisted_states().get(flag_id, False)


def list_flags() -> list[dict]:
    """Full Control Panel payload: static metadata + live enabled state."""
    states = _persisted_states()
    out = []
    for f in FLAG_DEFINITIONS:
        kill_switched = not _ENV_KILL_SWITCH.get(f["id"], True)
        enabled = states.get(f["id"], False) and not kill_switched
        out.append({**f, "enabled": enabled, "kill_switched": kill_switched})
    return out


def set_flag_state(flag_id: str, enabled: bool, actor: str) -> dict:
    """Persist a flag change. Caller (main.py) MUST admin-gate this — it
    changes app-wide behavior for every user, not just the caller's session."""
    if flag_id not in _flags_by_id:
        raise ValueError(f"Unknown flag_id: {flag_id}")
    _ensure_tables()
    enabled_sql = "TRUE" if enabled else "FALSE"
    safe_actor = (actor or "unknown").replace("'", "")
    _execute_sql(
        f"MERGE INTO {FLAGS_TABLE} t "
        f"USING (SELECT '{flag_id}' AS flag_id, {enabled_sql} AS enabled, "
        f"'{safe_actor}' AS updated_by, current_timestamp() AS updated_at) s "
        f"ON t.flag_id = s.flag_id "
        f"WHEN MATCHED THEN UPDATE SET * "
        f"WHEN NOT MATCHED THEN INSERT *"
    )
    _execute_sql(
        f"INSERT INTO {FLAGS_AUDIT_TABLE} VALUES "
        f"('{flag_id}', {enabled_sql}, '{safe_actor}', current_timestamp())"
    )
    logger.info(f"Feature flag {flag_id} set to {enabled} by {safe_actor}")
    return {"flag_id": flag_id, "enabled": bool(enabled)}


def check_access_requirements(flag_id: str) -> list[dict]:
    """Best-effort live check of each requirement. satisfied=None means we
    could not verify automatically (e.g. SHOW GRANTS unreadable) — this never
    raises, so an access-check failure can't block the Control Panel from
    rendering."""
    flag = _flags_by_id.get(flag_id)
    if not flag:
        raise ValueError(f"Unknown flag_id: {flag_id}")
    results = []
    for req in flag.get("access_requirements", []):
        satisfied: Optional[bool] = None
        detail = "Could not verify automatically — confirm with a workspace or Unity Catalog admin."
        try:
            if req["privilege"] == "CREATE TABLE" and "." in req["scope"] and req["scope"].count(".") == 1:
                rows = _execute_sql(f"SHOW GRANTS ON SCHEMA {req['scope']}")
                privs = {str(r.get("Privilege") or r.get("privilege") or "").upper() for r in rows}
                satisfied = bool(privs & {"CREATE TABLE", "ALL PRIVILEGES"})
                detail = "Verified via SHOW GRANTS ON SCHEMA." if satisfied else "SHOW GRANTS ON SCHEMA did not list this privilege for the current identity."
            elif req["privilege"] == "SELECT" and req["scope"].count(".") == 2:
                _execute_sql(f"SELECT 1 FROM {req['scope']} LIMIT 1")
                satisfied = True
                detail = "Verified — a 1-row SELECT against this table succeeded."
        except Exception as e:
            satisfied = False
            detail = f"Access check failed: {type(e).__name__} — likely missing this privilege."
        results.append({**req, "satisfied": satisfied, "detail": detail})
    return results
