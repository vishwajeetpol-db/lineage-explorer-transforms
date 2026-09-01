"""A1: per-user query identity, and the guarantee that it fails closed.

Note: conftest's `_never_build_a_real_workspace_client` neutralises both
WorkspaceClient and SdkConfig, so nothing here constructs a real client or resolves
auth metadata over the network.

Every data query in this app has historically run as the app's own service
principal, so Unity Catalog ACLs were not enforced on reads and the app's blast
radius was the union of every grant the SP held. ENFORCE_USER_IDENTITY routes
user-data reads through the caller's own token instead, making UC the perimeter.

It defaults OFF because turning it on makes every user see LESS than they do today.
These tests pin both halves: nothing changes while it is off, and while it is on the
SP client is never silently substituted for a missing caller identity.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.lineage_service as ls


@pytest.fixture(autouse=True)
def _clean():
    ls._reset_user_clients()
    ls.set_user_token("")
    yield
    ls._reset_user_clients()
    ls.set_user_token("")


class TestDefaultIsUnchanged:
    def test_flag_defaults_off(self):
        """A behaviour change this visible must be opt-in."""
        assert ls.ENFORCE_USER_IDENTITY is False

    def test_reads_use_the_app_client_when_off(self):
        sp = MagicMock(name="sp_client")
        with patch.object(ls, "ENFORCE_USER_IDENTITY", False), \
             patch.object(ls, "_get_client", return_value=sp):
            assert ls.get_read_client() is sp

    def test_a_user_token_is_ignored_when_off(self):
        """The token being present must not switch behaviour by itself."""
        sp = MagicMock(name="sp_client")
        ls.set_user_token("user-token")
        with patch.object(ls, "ENFORCE_USER_IDENTITY", False), \
             patch.object(ls, "_get_client", return_value=sp):
            assert ls.get_read_client() is sp


class TestEnforcementFailsClosed:
    def test_no_token_raises_instead_of_falling_back(self):
        """The whole point: never substitute the SP's wider visibility.

        A fallback here would mean the one case that matters — a request that
        arrived without an identity — silently got exactly the broad access the
        flag exists to remove.
        """
        sp = MagicMock(name="sp_client")
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True), \
             patch.object(ls, "_get_client", return_value=sp):
            with pytest.raises(ls.UserIdentityUnavailable, match="no user token"):
                ls.get_read_client()

    def test_error_names_the_cause_not_just_the_symptom(self):
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True):
            with pytest.raises(ls.UserIdentityUnavailable) as exc:
                ls.get_read_client()
        assert "ENFORCE_USER_IDENTITY" in str(exc.value)


class TestEnforcementUsesTheCallersToken:
    def test_client_is_built_from_the_forwarded_token(self):
        sp = MagicMock()
        sp.config.host = "https://ws.cloud.databricks.com"
        ls.set_user_token("caller-token-abc")
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True), \
             patch.object(ls, "_get_client", return_value=sp), \
             patch.object(ls, "WorkspaceClient") as MockWC:
            got = ls.get_read_client()
        assert got is MockWC.return_value
        cfg = MockWC.call_args.kwargs["config"]
        assert cfg.token == "caller-token-abc"
        assert cfg.host == "https://ws.cloud.databricks.com"

    def test_client_is_cached_per_token(self):
        """Construction resolves auth metadata; doing it per request adds a round-trip."""
        sp = MagicMock()
        sp.config.host = "https://ws.cloud.databricks.com"
        ls.set_user_token("tok-1")
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True), \
             patch.object(ls, "_get_client", return_value=sp), \
             patch.object(ls, "WorkspaceClient") as MockWC:
            ls.get_read_client()
            ls.get_read_client()
            assert MockWC.call_count == 1

    def test_different_users_get_different_clients(self):
        """A shared client would silently reintroduce the shared-identity bug."""
        sp = MagicMock()
        sp.config.host = "https://ws.cloud.databricks.com"
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True), \
             patch.object(ls, "_get_client", return_value=sp), \
             patch.object(ls, "WorkspaceClient") as MockWC:
            MockWC.side_effect = lambda **kw: MagicMock(name=kw["config"].token)
            ls.set_user_token("alice-token")
            a = ls.get_read_client()
            ls.set_user_token("bob-token")
            b = ls.get_read_client()
        assert a is not b
        assert MockWC.call_count == 2

    def test_client_cache_is_bounded(self):
        sp = MagicMock()
        sp.config.host = "https://ws.cloud.databricks.com"
        with patch.object(ls, "ENFORCE_USER_IDENTITY", True), \
             patch.object(ls, "_USER_CLIENT_MAX", 5), \
             patch.object(ls, "_get_client", return_value=sp), \
             patch.object(ls, "WorkspaceClient", MagicMock()):
            for i in range(25):
                ls.set_user_token(f"tok-{i}")
                ls.get_read_client()
        assert len(ls._user_clients) <= 5


class TestAppOwnedPathsKeepTheSPClient:
    def test_diagnostics_probes_as_the_app(self):
        """It answers "can the APP reach its prerequisites" — a caller's grants would
        make a correctly-configured deploy look broken to a non-admin."""
        import ast, inspect, textwrap
        tree = ast.parse(textwrap.dedent(inspect.getsource(ls.run_diagnostics)))
        # Compare CODE, not source text: the docstring names get_read_client to explain
        # why it is not used, and a substring check on the raw source matches that prose.
        called = {
            n.func.id for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_get_client" in called
        assert "get_read_client" not in called

    def test_app_client_is_still_a_singleton(self):
        ls._client_instance = None
        with patch.object(ls, "WorkspaceClient", MagicMock()) as MockWC:
            first = ls._get_client()
            second = ls._get_client()
        assert first is second and MockWC.call_count == 1
        ls._client_instance = None
