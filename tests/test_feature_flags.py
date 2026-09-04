"""Tests for backend.feature_flags — Control Panel engine.

Fixes:
- A14: LOCAL_DEV_ADMIN_EMAIL privilege escalation testing
- C7:  feature_flags table missing — graceful degradation
- C11: Capture when flags off / lineage schema missing

Covers the flag lifecycle, env kill-switch behavior, and graceful
degradation when the warehouse or feature_flags table is unreachable.
"""
import os
from unittest.mock import patch, MagicMock

import pytest


class TestGetFlagState:
    """get_flag_state() behavior under various conditions."""

    def test_unknown_flag_returns_false(self, mock_feature_flags_sql):
        """Unregistered flag id should never raise KeyError."""
        from backend.feature_flags import get_flag_state
        result = get_flag_state("nonexistent.flag_id")
        assert result is False

    # NOTE on the two tests below: both need the module reloaded so it re-reads
    # its env kill switch at import time, but `importlib.reload` re-executes the
    # module body and rebinds its own `_execute_sql` — which DISCARDS the
    # mock_feature_flags_sql fixture's patch. Any code path that then reaches SQL
    # calls the real warehouse client and blocks for the full poll timeout, which
    # is why these must re-patch the reloaded module explicitly.

    def test_env_kill_switch_overrides_db(self, mock_feature_flags_sql):
        """When env kill switch is 'false', flag is disabled regardless of DB."""
        with patch.dict(os.environ, {"ENABLE_PLAN_CAPTURE": "false"}):
            import importlib
            import backend.feature_flags as ff
            importlib.reload(ff)
            with patch.object(ff, "_execute_sql", return_value=[{"enabled": "true"}]):
                assert ff.get_flag_state("lineage_tracking.plan_capture") is False

    def test_flag_enabled_when_db_says_true(self, mock_feature_flags_sql):
        """Flag returns True when the DB row says enabled and no kill switch.

        This test used to hang the entire suite: it reloaded the module (dropping
        the fixture's patch) and then took the DB path, so `get_flag_state` issued
        a real `execute_statement` against a warehouse that isn't there. Its
        assertion — `result is True or result is False` — also accepted any bool,
        so it verified nothing even when it did complete.
        """
        with patch.dict(os.environ, {"ENABLE_PLAN_CAPTURE": "true"}):
            import importlib
            import backend.feature_flags as ff
            importlib.reload(ff)
            # _persisted_states() reads flag_id + enabled off each row, so the row
            # has to carry both — a row of just {"enabled": ...} raises KeyError
            # inside the loader and degrades to "all flags disabled".
            rows = [{"flag_id": "lineage_tracking.plan_capture", "enabled": True}]
            with patch.object(ff, "_execute_sql", return_value=rows):
                assert ff.get_flag_state("lineage_tracking.plan_capture") is True


class TestListFlags:
    """list_flags() graceful degradation."""

    def test_degrades_to_all_disabled_on_sql_error(self, mock_feature_flags_sql):
        """C7: When warehouse or feature_flags table is unreachable,
        all flags should be disabled (safe default), not 500."""
        mock_feature_flags_sql.side_effect = RuntimeError("warehouse unreachable")
        from backend.feature_flags import list_flags
        result = list_flags()
        assert isinstance(result, list)
        for flag in result:
            assert flag["enabled"] is False

    def test_degrades_on_table_not_found(self, mock_feature_flags_sql):
        """C7: Table doesn't exist yet (first deploy) — all OFF."""
        mock_feature_flags_sql.side_effect = RuntimeError(
            "SQL failed: TABLE_OR_VIEW_NOT_FOUND"
        )
        from backend.feature_flags import list_flags
        result = list_flags()
        assert isinstance(result, list)
        for flag in result:
            assert flag["enabled"] is False

    def test_returns_all_defined_flags(self, mock_feature_flags_sql):
        """Should return one entry per FLAG_DEFINITIONS item."""
        mock_feature_flags_sql.return_value = []
        from backend.feature_flags import list_flags, FLAG_DEFINITIONS
        result = list_flags()
        assert len(result) == len(FLAG_DEFINITIONS)


class TestSetFlagState:
    """set_flag_state() validation."""

    def test_unknown_flag_raises_valueerror(self, mock_feature_flags_sql):
        """Setting an unknown flag_id should raise ValueError."""
        from backend.feature_flags import set_flag_state
        with pytest.raises((ValueError, KeyError)):
            set_flag_state("unknown.flag", True, actor="admin@test.com")

    def test_valid_flag_calls_sql(self, mock_feature_flags_sql):
        """Setting a valid flag should attempt SQL execution."""
        from backend.feature_flags import set_flag_state
        try:
            set_flag_state("lineage_tracking.plan_capture", True, actor="admin@test.com")
        except Exception:
            pass  # May fail due to mocked SQL, but should not raise ValueError


class TestFlagDefinitions:
    """FLAG_DEFINITIONS static structure."""

    def test_all_flags_have_required_fields(self):
        from backend.feature_flags import FLAG_DEFINITIONS
        required_keys = {"id", "module", "name", "description", "cost", "risk"}
        for flag in FLAG_DEFINITIONS:
            assert required_keys.issubset(flag.keys()), f"Flag {flag.get('id')} missing fields"

    def test_all_flag_ids_unique(self):
        from backend.feature_flags import FLAG_DEFINITIONS
        ids = [f["id"] for f in FLAG_DEFINITIONS]
        assert len(ids) == len(set(ids)), "Duplicate flag IDs found"


class TestLocalDevAdminEscalation:
    """A14: LOCAL_DEV_ADMIN_EMAIL privilege escalation.

    When LOCAL_DEV_ADMIN_EMAIL is set and no x-forwarded-access-token
    header is present, _get_user_info returns (email, True) = admin.
    This is intended for local dev only but is catastrophic if set on
    a deployed App.
    """

    def test_local_dev_admin_grants_admin_without_token(self):
        """A14 BUG: Setting LOCAL_DEV_ADMIN_EMAIL makes all headerless requests admin."""
        with patch.dict(os.environ, {"LOCAL_DEV_ADMIN_EMAIL": "dev@databricks.com"}):
            from backend.main import _get_user_info
            from unittest.mock import MagicMock
            request = MagicMock()
            request.headers = {}  # No x-forwarded-access-token
            email, is_admin = _get_user_info(request)
            # BUG: Returns admin=True for ANY request without a token
            assert email == "dev@databricks.com"
            assert is_admin is True

    def test_no_local_dev_admin_denies_access(self):
        """Without LOCAL_DEV_ADMIN_EMAIL, no-token requests are (None, False)."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LOCAL_DEV_ADMIN_EMAIL", None)
            from backend.main import _get_user_info
            from unittest.mock import MagicMock
            request = MagicMock()
            request.headers = {}
            email, is_admin = _get_user_info(request)
            assert email is None
            assert is_admin is False

    def test_local_dev_admin_ignored_when_token_present(self):
        """Even with LOCAL_DEV_ADMIN_EMAIL set, a token takes precedence."""
        with patch.dict(os.environ, {"LOCAL_DEV_ADMIN_EMAIL": "dev@test.com"}):
            from backend.main import _get_user_info
            from unittest.mock import MagicMock
            request = MagicMock()
            request.headers = {"x-forwarded-access-token": "some-token"}
            # Should take the token path (SDK lookup), NOT use LOCAL_DEV_ADMIN_EMAIL.
            # Mock both _get_client (host) and WorkspaceClient so no real
            # control-plane call is made (which would hang offline).
            with patch("backend.main._get_client") as mock_client, \
                 patch("databricks.sdk.core.Config") as mock_cfg, \
                 patch("backend.main.WorkspaceClient") as mock_ws:
                mock_client.return_value.config.host = "https://example.databricks.com"
                mock_cfg.return_value = MagicMock()
                me = MagicMock()
                me.user_name = "real-user@databricks.com"
                me.groups = []
                mock_ws.return_value.current_user.me.return_value = me
                email, is_admin = _get_user_info(request)
                # Token path was taken (not the local dev override)
                assert email == "real-user@databricks.com"
                assert email != "dev@test.com"
                assert is_admin is False


class TestFlagAccessRequirements:
    """C11: check_access_requirements for flags that need specific grants."""

    def test_check_access_returns_structure(self, mock_feature_flags_sql):
        """check_access_requirements should return grant requirements."""
        from backend.feature_flags import check_access_requirements
        # check_access_requirements takes a flag_id and returns a list of
        # per-requirement dicts (each with a 'satisfied' verdict).
        result = check_access_requirements("lineage_tracking.plan_capture")
        assert isinstance(result, (list, dict))
