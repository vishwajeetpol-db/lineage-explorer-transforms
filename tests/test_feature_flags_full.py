"""Additional coverage for backend.feature_flags (adds to test_feature_flags.py)."""
from unittest.mock import patch

import pytest

import backend.feature_flags as ff


class TestGetFlagState:
    def test_unknown_flag_false(self):
        assert ff.get_flag_state("no.such.flag") is False

    def test_known_flag_reads_persisted(self):
        fid = ff.FLAG_DEFINITIONS[0]["id"]
        with patch.object(ff, "_persisted_states", return_value={fid: True}):
            assert ff.get_flag_state(fid) is True

    def test_kill_switch_forces_false(self):
        fid = ff.FLAG_DEFINITIONS[0]["id"]
        with patch.dict(ff._ENV_KILL_SWITCH, {fid: False}, clear=False), \
             patch.object(ff, "_persisted_states", return_value={fid: True}):
            assert ff.get_flag_state(fid) is False


class TestListFlags:
    def test_returns_all_definitions(self):
        with patch.object(ff, "_persisted_states", return_value={}):
            out = ff.list_flags()
        assert len(out) == len(ff.FLAG_DEFINITIONS)
        assert all("enabled" in f and "kill_switched" in f for f in out)

    def test_degrades_on_sql_error(self):
        with patch.object(ff, "_ensure_tables"), \
             patch.object(ff, "_execute_sql", side_effect=RuntimeError("no table")):
            # _persisted_states swallows -> {} ; list_flags still returns defs
            out = ff.list_flags()
        assert len(out) == len(ff.FLAG_DEFINITIONS)


class TestSetFlagState:
    def test_unknown_raises_valueerror(self):
        with pytest.raises(ValueError):
            ff.set_flag_state("no.such.flag", True, actor="a@b.com")

    def test_valid_persists(self):
        fid = ff.FLAG_DEFINITIONS[0]["id"]
        with patch.object(ff, "_ensure_tables"), \
             patch.object(ff, "_execute_sql", return_value=[]) as mock_sql:
            out = ff.set_flag_state(fid, True, actor="a@b.com")
        assert out["flag_id"] == fid and out["enabled"] is True
        assert mock_sql.call_count >= 2  # MERGE + audit INSERT


class TestCheckAccessRequirements:
    def test_unknown_raises(self):
        with pytest.raises(ValueError):
            ff.check_access_requirements("no.such.flag")

    def test_returns_requirement_list(self):
        fid = ff.FLAG_DEFINITIONS[0]["id"]
        with patch.object(ff, "_get_client", return_value=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()), \
             patch.object(ff, "_execute_sql", return_value=[]):
            out = ff.check_access_requirements(fid)
        assert isinstance(out, list)


class TestPersistedStates:
    def test_reads_rows(self):
        with patch.object(ff, "_ensure_tables"), \
             patch.object(ff, "_execute_sql", return_value=[{"flag_id": "x", "enabled": True}]):
            assert ff._persisted_states() == {"x": True}

    def test_error_returns_empty(self):
        with patch.object(ff, "_ensure_tables"), \
             patch.object(ff, "_execute_sql", side_effect=RuntimeError("boom")):
            assert ff._persisted_states() == {}


class TestLiveBilling:
    def test_billing_shape_or_degrades(self):
        with patch.object(ff, "_get_client", return_value=__import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()), \
             patch.object(ff, "_execute_sql", return_value=[]):
            out = ff.get_capability_live_billing(ff.FLAG_DEFINITIONS[0]["id"])
        assert isinstance(out, dict)
