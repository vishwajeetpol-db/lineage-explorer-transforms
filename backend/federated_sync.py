"""Federated Sync — first-cut cross-workspace/cross-metastore lineage registry.

Gated by the `federated_sync.cross_workspace` feature flag
(backend/feature_flags.py). Builds on the EXISTING Delta Sharing overlay
(backend/lineage_service.get_sharing_overview) rather than duplicating it:
this module adds a small admin-managed registry of "known peer"
workspaces/metastores and cross-references it against the sharing metadata
already surfaced, so a shared boundary node can be flagged as a known peer
instead of an anonymous share.

Scope note (see docs/architecture.md, Known Gaps): this is a v1 scaffold. It
does NOT implement live cross-workspace API calls, trust handshakes, or
peer-initiated sync jobs — those require the full FEDERATED_LINEAGE_DESIGN
spec, which was provided only as a binary .docx the assistant that built this
module could not read. Treat `list_federated_peers` as an admin-curated
registry, not a live discovery mechanism.
"""
from __future__ import annotations

import os
import logging

from databricks.sdk.service.sql import StatementState
from backend.lineage_service import _get_client, get_sharing_overview
from backend.feature_flags import get_flag_state

logger = logging.getLogger(__name__)

LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")
PEERS_TABLE = f"{LINEAGE_CATALOG}.{LINEAGE_SCHEMA}.federated_peers"
WAREHOUSE_ID = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
SQL_WAIT_TIMEOUT = os.environ.get("SQL_WAIT_TIMEOUT", "50s")

_VALID_DIRECTIONS = ("inbound", "outbound", "both")


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


def _ensure_table() -> None:
    try:
        _execute_sql(
            f"CREATE TABLE IF NOT EXISTS {PEERS_TABLE} ("
            f"peer_alias STRING, share_name STRING, direction STRING, "
            f"registered_by STRING, registered_at TIMESTAMP, notes STRING"
            f") USING DELTA"
        )
    except Exception as e:
        logger.warning(f"federated_sync: could not ensure {PEERS_TABLE} (non-fatal): {e}")


def list_federated_peers() -> list[dict]:
    """Registered peers. Returns [] (not an error) when the flag is off or the
    table isn't reachable yet — this is a read path, never fails the caller."""
    if not get_flag_state("federated_sync.cross_workspace"):
        return []
    try:
        _ensure_table()
        return _execute_sql(f"SELECT * FROM {PEERS_TABLE} ORDER BY registered_at DESC")
    except Exception as e:
        logger.info(f"federated_sync: no peers readable yet: {e}")
        return []


def register_federated_peer(peer_alias: str, share_name: str, direction: str, actor: str, notes: str = "") -> dict:
    """Admin-curated registration. Caller (main.py) MUST admin-gate this."""
    _ensure_table()
    direction = direction if direction in _VALID_DIRECTIONS else "both"
    safe = lambda s: (s or "").replace("'", "")
    _execute_sql(
        f"INSERT INTO {PEERS_TABLE} VALUES ("
        f"'{safe(peer_alias)}', '{safe(share_name)}', '{direction}', "
        f"'{safe(actor)}', current_timestamp(), '{safe(notes)}')"
    )
    return {"peer_alias": peer_alias, "share_name": share_name, "direction": direction}


def get_federated_sync_status() -> dict:
    """Guarded status for the Control Panel card — never raises. Cross-references
    registered peers against the existing sharing overview's known share/foreign
    catalog names to report how many registered peers currently resolve to a
    real, visible Delta Share."""
    enabled = get_flag_state("federated_sync.cross_workspace")
    status = {"enabled": enabled, "registered_peers": 0, "known_shares": 0, "reachable_overlap": 0}
    if not enabled:
        return status
    try:
        peers = list_federated_peers()
        status["registered_peers"] = len(peers)
        overview = get_sharing_overview(False)
        overview_shares: set[str] = set()
        if isinstance(overview, dict):
            for key in ("shared_out", "shared_in", "shares", "foreign_catalogs"):
                items = overview.get(key)
                if items:
                    for it in items:
                        nm = (it.get("share_name") or it.get("name")) if isinstance(it, dict) else None
                        if nm:
                            overview_shares.add(nm)
        known_shares = {p.get("share_name") for p in peers if p.get("share_name")}
        status["known_shares"] = len(overview_shares)
        status["reachable_overlap"] = len(known_shares & overview_shares)
    except Exception as e:
        logger.info(f"federated_sync: status computation degraded (non-fatal): {e}")
    return status
