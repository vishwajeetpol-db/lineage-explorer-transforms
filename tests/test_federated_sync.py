"""Unit tests for backend.federated_sync.

Covers:
- _execute_sql (no-warehouse, success, failed, empty)
- _ensure_table (success + swallowed error)
- list_federated_peers (flag off, happy, error)
- register_federated_peer (valid direction, invalid->both)
- verify_peer_trust (flag off, no peer, no share_name, no provider url,
  live HTTP success, HTTP error)
- trigger_peer_sync_job (flag off, no peer, sync_job_id present, naming
  convention found, no job found, error)
- get_federated_sync_status (flag off, happy overlap, degraded)
"""
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState

import backend.federated_sync as fs


def _make_client(columns=None, data=None, state=StatementState.SUCCEEDED, error_msg=None):
    resp = MagicMock()
    resp.status.state = state
    if error_msg is not None:
        resp.status.error.message = error_msg
    else:
        resp.status.error = None
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        cols = []
        for name in (columns or []):
            c = MagicMock()
            c.name = name
            cols.append(c)
        resp.manifest.schema.columns = cols
    client = MagicMock()
    client.statement_execution.execute_statement.return_value = resp
    return client


class TestExecuteSql:
    def test_no_warehouse(self):
        with patch.object(fs, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                fs._execute_sql("SELECT 1")

    def test_success(self):
        client = _make_client(columns=["a"], data=[["v"]])
        with patch.object(fs, "WAREHOUSE_ID", "wh"), patch.object(fs, "_get_client", return_value=client):
            assert fs._execute_sql("SELECT a") == [{"a": "v"}]

    def test_failed(self):
        client = _make_client(state=StatementState.FAILED, error_msg="e")
        with patch.object(fs, "WAREHOUSE_ID", "wh"), patch.object(fs, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="e"):
                fs._execute_sql("SELECT 1")

    def test_empty(self):
        client = _make_client(data=None)
        with patch.object(fs, "WAREHOUSE_ID", "wh"), patch.object(fs, "_get_client", return_value=client):
            assert fs._execute_sql("SELECT 1") == []


class TestEnsureTable:
    def test_success(self):
        with patch.object(fs, "_execute_sql", return_value=[]) as m:
            fs._ensure_table()
        assert "CREATE TABLE IF NOT EXISTS" in m.call_args[0][0]

    def test_error_swallowed(self):
        with patch.object(fs, "_execute_sql", side_effect=RuntimeError("x")):
            fs._ensure_table()


class TestListFederatedPeers:
    def test_flag_off(self):
        with patch.object(fs, "get_flag_state", return_value=False):
            assert fs.list_federated_peers() == []

    def test_happy(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "_ensure_table"), \
             patch.object(fs, "_execute_sql", return_value=[{"peer_alias": "p"}]):
            assert fs.list_federated_peers() == [{"peer_alias": "p"}]

    def test_error(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "_ensure_table"), \
             patch.object(fs, "_execute_sql", side_effect=RuntimeError("x")):
            assert fs.list_federated_peers() == []


class TestRegisterFederatedPeer:
    def test_valid_direction(self):
        with patch.object(fs, "_ensure_table"), patch.object(fs, "_execute_sql", return_value=[]) as m:
            out = fs.register_federated_peer("p", "sh", "inbound", "actor", notes="n'x")
        assert out == {"peer_alias": "p", "share_name": "sh", "direction": "inbound"}
        assert "INSERT INTO" in m.call_args[0][0]

    def test_invalid_direction_defaults_both(self):
        with patch.object(fs, "_ensure_table"), patch.object(fs, "_execute_sql", return_value=[]):
            out = fs.register_federated_peer("p", "sh", "sideways", "a")
        assert out["direction"] == "both"


class TestVerifyPeerTrust:
    def test_flag_off(self):
        with patch.object(fs, "get_flag_state", return_value=False):
            out = fs.verify_peer_trust("p")
        assert out["error"] == "federated_sync flag is disabled"

    def test_no_peer(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=[]):
            out = fs.verify_peer_trust("p")
        assert "No registered peer" in out["error"]

    def test_no_share_name(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=[{"peer_alias": "p"}]):
            out = fs.verify_peer_trust("p")
        assert "no share_name" in out["error"]

    def test_no_provider_url(self):
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "get_sharing_overview", return_value={"providers": []}):
            out = fs.verify_peer_trust("p")
        assert out["reachable"] is None
        assert "verification skipped" in out["error"]

    @staticmethod
    def _probe_ctx(peers, overview, urlopen_kw):
        """Patches shared by the probe tests.

        `assert_safe_outbound_url` is stubbed to pass through so these tests exercise
        the PROBE. The validator has its own tests in test_validators.py, and leaving
        it live here would reject the fixtures' unresolvable `prov` host before the
        probe ever ran.
        """
        return (
            patch.object(fs, "get_flag_state", return_value=True),
            patch.object(fs, "list_federated_peers", return_value=peers),
            patch.object(fs, "get_sharing_overview", return_value=overview),
            patch.object(fs, "assert_safe_outbound_url", side_effect=lambda u, *a, **k: u),
            patch("urllib.request.urlopen", **urlopen_kw),
        )

    def test_live_http_success(self):
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        overview = {"providers": [{"name": "sh", "sharing_server_url": "https://prov/"}]}
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        cm = MagicMock()
        cm.__enter__.return_value = fake_resp
        cm.__exit__.return_value = False
        a, b, c, d, e = self._probe_ctx(peers, overview, {"return_value": cm})
        with a, b, c, d, e:
            out = fs.verify_peer_trust("p")
        assert out["reachable"] is True
        assert isinstance(out["latency_ms"], int)

    def test_http_error(self):
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        overview = {"providers": [{"name": "sh", "sharing_server_url": "https://prov"}]}
        a, b, c, d, e = self._probe_ctx(peers, overview, {"side_effect": RuntimeError("conn refused")})
        with a, b, c, d, e:
            out = fs.verify_peer_trust("p")
        assert out["reachable"] is False
        assert "conn refused" in out["error"]

    def test_probe_sends_no_credential(self):
        """The app must never hand its own token to a metastore-supplied host.

        `sharing_server_url` is chosen by whoever registered the sharing provider.
        This probe used to carry `Authorization: Bearer {client.config.token}` — the
        app SP's token, which is the key to everything the app can read, since every
        data query runs as that SP. A reachability probe needs no credential.
        """
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        overview = {"providers": [{"name": "sh", "sharing_server_url": "https://prov"}]}
        fake_resp = MagicMock()
        fake_resp.read.return_value = b"{}"
        cm = MagicMock()
        cm.__enter__.return_value = fake_resp
        cm.__exit__.return_value = False
        a, b, c, d, e = self._probe_ctx(peers, overview, {"return_value": cm})
        with a, b, c, d, e as mock_open:
            fs.verify_peer_trust("p")

        req = mock_open.call_args[0][0]
        headers = {k.lower(): v for k, v in req.header_items()}
        assert "authorization" not in headers, f"credential leaked: {headers}"
        assert not any("bearer" in str(v).lower() for v in headers.values())

    def test_unauthenticated_401_still_counts_as_reachable(self):
        """An endpoint that answers 401 is up — which is all this function reports."""
        import urllib.error
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        overview = {"providers": [{"name": "sh", "sharing_server_url": "https://prov"}]}
        err = urllib.error.HTTPError("https://prov/shares/sh", 401, "Unauthorized", {}, None)
        a, b, c, d, e = self._probe_ctx(peers, overview, {"side_effect": err})
        with a, b, c, d, e:
            out = fs.verify_peer_trust("p")
        assert out["reachable"] is True

    def test_unsafe_provider_url_is_refused_before_connecting(self):
        """SSRF: a private/metadata address must be rejected without a request."""
        peers = [{"peer_alias": "p", "share_name": "sh"}]
        overview = {"providers": [
            {"name": "sh", "sharing_server_url": "https://169.254.169.254"}
        ]}
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "get_sharing_overview", return_value=overview), \
             patch("urllib.request.urlopen") as mock_open:
            out = fs.verify_peer_trust("p")
        mock_open.assert_not_called()
        assert out["reachable"] is False
        assert "unsafe" in out["error"].lower()


class TestTriggerPeerSyncJob:
    def test_flag_off(self):
        with patch.object(fs, "get_flag_state", return_value=False):
            assert fs.trigger_peer_sync_job("p", "a")["error"].startswith("federated_sync flag")

    def test_no_peer(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=[]):
            assert "No registered peer" in fs.trigger_peer_sync_job("p", "a")["error"]

    def test_with_stored_job_id(self):
        peers = [{"peer_alias": "p", "sync_job_id": "42"}]
        client = MagicMock()
        client.jobs.run_now.return_value = MagicMock(run_id=99)
        client.config.host = "https://host/"
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "_get_client", return_value=client):
            out = fs.trigger_peer_sync_job("p", "actor")
        assert out["job_id"] == "42"
        assert out["run_id"] == 99
        assert "/#job/42/run/99" in out["run_url"]

    def test_naming_convention_found(self):
        peers = [{"peer_alias": "p"}]
        client = MagicMock()
        client.jobs.list.return_value = [MagicMock(job_id=7)]
        client.jobs.run_now.return_value = MagicMock(run_id=1)
        client.config.host = "https://host"
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "_get_client", return_value=client):
            out = fs.trigger_peer_sync_job("p", "actor")
        assert out["job_id"] == "7"

    def test_no_job_found(self):
        peers = [{"peer_alias": "p"}]
        client = MagicMock()
        client.jobs.list.return_value = []
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "_get_client", return_value=client):
            out = fs.trigger_peer_sync_job("p", "actor")
        assert "No sync job found" in out["error"]

    def test_error(self):
        peers = [{"peer_alias": "p", "sync_job_id": "1"}]
        client = MagicMock()
        client.jobs.run_now.side_effect = RuntimeError("run fail")
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "_get_client", return_value=client):
            out = fs.trigger_peer_sync_job("p", "actor")
        assert "run fail" in out["error"]


class TestGetFederatedSyncStatus:
    def test_flag_off(self):
        with patch.object(fs, "get_flag_state", return_value=False):
            out = fs.get_federated_sync_status()
        assert out["enabled"] is False
        assert out["registered_peers"] == 0

    def test_happy_overlap(self):
        peers = [{"share_name": "sh1"}, {"share_name": "sh2"}, {"share_name": None}]
        overview = {
            "shared_out": [{"share_name": "sh1"}],
            "shares": [{"name": "sh2"}],
            "foreign_catalogs": [{"name": "other"}],
            "shared_in": None,
        }
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", return_value=peers), \
             patch.object(fs, "get_sharing_overview", return_value=overview):
            out = fs.get_federated_sync_status()
        assert out["registered_peers"] == 3
        assert out["known_shares"] == 3  # sh1, sh2, other
        assert out["reachable_overlap"] == 2  # sh1 & sh2

    def test_degraded(self):
        with patch.object(fs, "get_flag_state", return_value=True), \
             patch.object(fs, "list_federated_peers", side_effect=RuntimeError("x")):
            out = fs.get_federated_sync_status()
        assert out["enabled"] is True
        assert out["registered_peers"] == 0
