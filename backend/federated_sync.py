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
from backend.validators import UnsafeOutboundURL, assert_safe_outbound_url

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


# ---------------------------------------------------------------------------
# Cap 32 — Live peer trust handshakes and peer-initiated sync jobs
# ---------------------------------------------------------------------------

def verify_peer_trust(peer_alias: str) -> dict:
    """Attempt a live connectivity and metadata handshake with a registered peer.

    Handshake protocol:
      1. Load the peer record from federated_peers.
      2. Look up the peer's share in the existing sharing overview to find the
         provider workspace URL embedded in the Delta Sharing profile.
      3. Issue a GET /shares/<share_name> against the provider REST endpoint
         using the current SPN token to verify the share is reachable.
      4. Return {reachable: bool, latency_ms: int, error: str|None}.

    Non-fatal — always returns a dict.
    """
    import time as _time
    base = {"peer_alias": peer_alias, "reachable": False, "latency_ms": None, "error": None}
    if not get_flag_state("federated_sync.cross_workspace"):
        base["error"] = "federated_sync flag is disabled"
        return base
    try:
        peers = list_federated_peers()
        peer = next((p for p in peers if p.get("peer_alias") == peer_alias), None)
        if not peer:
            base["error"] = f"No registered peer with alias '{peer_alias}'"
            return base
        share_name = peer.get("share_name")
        if not share_name:
            base["error"] = "Peer record has no share_name; cannot locate provider endpoint"
            return base

        # Try to verify via the Delta Sharing REST protocol (providers expose
        # GET /shares/<name> on the sharing endpoint registered in the profile).
        # We resolve the endpoint URL from the Unity Catalog sharing view.
        overview = get_sharing_overview(False)
        provider_url = None
        if isinstance(overview, dict):
            for p_info in (overview.get("providers") or []):
                if isinstance(p_info, dict) and p_info.get("name") == share_name:
                    provider_url = p_info.get("sharing_server_url")
                    break

        if not provider_url:
            # Cannot verify without a known endpoint; mark as unverifiable
            base["reachable"] = None  # type: ignore[assignment]  # None = unknown
            base["error"] = "Provider endpoint URL not found in sharing overview; verification skipped"
            return base

        # Live HTTP probe.
        #
        # TWO controls here, and the first one is the important one:
        #
        # 1. NO CREDENTIAL. This request goes to a host named by `sharing_server_url`
        #    in Unity Catalog metadata — chosen by whoever registered the sharing
        #    provider, not by us. It previously carried
        #    `Authorization: Bearer {client.config.token}`, the app service
        #    principal's own token, which handed that credential to any host a
        #    provider registration named. Since every data query in this app runs as
        #    that SP, the token is the key to everything the app can read. A
        #    reachability probe does not need to authenticate: an unauthenticated
        #    401/403 still proves the endpoint is up, which is all this reports.
        #
        # 2. URL validation before connecting — https only, no embedded credentials,
        #    and rejected if the host resolves to a private, loopback, link-local or
        #    reserved address. Without it the same unvalidated URL reached the app's
        #    network position, including cloud instance-metadata endpoints (SSRF).
        import urllib.error
        import urllib.request
        try:
            probe_url = assert_safe_outbound_url(
                f"{provider_url.rstrip('/')}/shares/{share_name}", "provider endpoint"
            )
        except UnsafeOutboundURL as e:
            logger.warning("Refused to probe peer %s: %s", peer_alias, e)
            base["reachable"] = False
            base["error"] = f"Provider endpoint rejected as unsafe: {e}"
            return base

        req = urllib.request.Request(probe_url, method="GET")
        t0 = _time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read(1024)
        except urllib.error.HTTPError:
            # The endpoint answered — with 401/403 for an unauthenticated probe, most
            # likely. That is reachability, which is what this function reports.
            pass
        latency_ms = int((_time.time() - t0) * 1000)
        base["reachable"] = True
        base["latency_ms"] = latency_ms
        return base
    except Exception as e:
        base["reachable"] = False
        base["error"] = str(e)[:200]
        return base


def trigger_peer_sync_job(peer_alias: str, actor: str) -> dict:
    """Trigger a Lakeflow Job that executes the cross-workspace sync for a peer.

    The sync job is expected to be a pre-configured Lakeflow Job whose name
    is stored as a `sync_job_id` in the federated_peers table, or whose name
    follows the convention `bricktrace_federated_sync_<peer_alias>`.

    Returns {job_id, run_id, run_url} on success, or {error} on failure.
    Non-fatal.
    """
    if not get_flag_state("federated_sync.cross_workspace"):
        return {"error": "federated_sync flag is disabled"}
    try:
        peers = list_federated_peers()
        peer = next((p for p in peers if p.get("peer_alias") == peer_alias), None)
        if not peer:
            return {"error": f"No registered peer with alias '{peer_alias}'"}

        # Resolve job: check for a stored sync_job_id column (forward-compatible)
        job_id_str = peer.get("sync_job_id")
        client = _get_client()
        if not job_id_str:
            # Try to find by naming convention
            job_name = f"bricktrace_federated_sync_{peer_alias}"
            jobs = list(client.jobs.list(name=job_name))
            if not jobs:
                return {"error": f"No sync job found for peer '{peer_alias}'. Create a job named '{job_name}' or add a sync_job_id column to {PEERS_TABLE}."}
            job_id_str = str(jobs[0].job_id)

        run = client.jobs.run_now(job_id=int(job_id_str))
        run_id = run.run_id
        run_url = f"{client.config.host.rstrip('/')}/#job/{job_id_str}/run/{run_id}"
        logger.info(f"federated_sync: triggered sync job {job_id_str} run {run_id} for peer {peer_alias} by {actor}")
        return {"job_id": job_id_str, "run_id": run_id, "run_url": run_url}
    except Exception as e:
        logger.warning(f"federated_sync: trigger_peer_sync_job failed for {peer_alias}: {e}")
        return {"error": str(e)[:300]}


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
