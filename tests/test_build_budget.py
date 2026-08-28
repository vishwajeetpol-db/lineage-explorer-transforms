"""C2: serverless build ceilings.

The admin gate stops non-admins spending money; it does nothing about an admin
spending it. The per-table lock only stops DUPLICATE builds of the same table, so an
admin working down a 200-table schema submits 200 legitimately-distinct serverless
runs as fast as the UI allows. In an enterprise "admin" is a group, not one careful
person, and the bill arrives with no owner attached.
"""
from unittest.mock import MagicMock, patch

import pytest

import backend.build_service as bs


NB = "/Workspace/Users/deployer/.bundle/bricktrace/dev/files/notebooks/run_pipeline"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("PIPELINE_NOTEBOOK_PATH", NB)
    bs._reset_pipeline_notebook_path_cache()
    bs._reset_source_access_cache()
    bs._reset_build_budget()
    bs._build_locks.clear()
    yield
    bs._reset_pipeline_notebook_path_cache()
    bs._reset_source_access_cache()
    bs._reset_build_budget()
    bs._build_locks.clear()


def _client():
    c = MagicMock()
    c.config.host = "https://test.databricks.com"
    c.config.authenticate.return_value = {"Authorization": "Bearer x"}
    return c


def _ok_post():
    r = MagicMock()
    r.json.return_value = {"run_id": 4242}
    r.raise_for_status = MagicMock()
    return r


class TestInFlightCap:
    def test_burst_is_capped(self):
        with patch.object(bs, "MAX_BUILDS_IN_FLIGHT", 3), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            for i in range(3):
                bs.submit_build_job(f"cat.sch.t{i}", actor="admin@x.com")
            with pytest.raises(bs.BuildBudgetError, match="already running"):
                bs.submit_build_job("cat.sch.t99", actor="admin@x.com")

    def test_a_duplicate_is_still_a_duplicate_not_a_budget_error(self):
        """The duplicate check must win: it is the more specific, more useful message."""
        with patch.object(bs, "MAX_BUILDS_IN_FLIGHT", 1), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.same", actor="a@x.com")
            with pytest.raises(RuntimeError, match="already in progress"):
                bs.submit_build_job("cat.sch.same", actor="a@x.com")

    def test_completed_builds_free_capacity(self):
        with patch.object(bs, "MAX_BUILDS_IN_FLIGHT", 1), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.a", actor="a@x.com")
            bs._build_locks.clear()          # as get_build_status does on completion
            bs.submit_build_job("cat.sch.b", actor="a@x.com")


class TestDailyBudget:
    def test_daily_ceiling_enforced(self):
        with patch.object(bs, "MAX_BUILDS_PER_DAY", 2), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.a", actor="a@x.com")
            bs._build_locks.clear()
            bs.submit_build_job("cat.sch.b", actor="a@x.com")
            bs._build_locks.clear()
            with pytest.raises(bs.BuildBudgetError, match="daily build budget"):
                bs.submit_build_job("cat.sch.c", actor="a@x.com")

    def test_a_rejected_duplicate_costs_no_budget(self):
        with patch.object(bs, "MAX_BUILDS_PER_DAY", 5), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.a", actor="a@x.com")
            with pytest.raises(RuntimeError, match="already in progress"):
                bs.submit_build_job("cat.sch.a", actor="a@x.com")
        assert bs.get_build_budget()["builds_today"] == 1

    def test_a_failed_submit_is_refunded(self):
        """A build that never launched must not consume budget for the rest of the day."""
        with patch.object(bs, "MAX_BUILDS_PER_DAY", 5), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                bs.submit_build_job("cat.sch.a", actor="a@x.com")
        assert bs.get_build_budget()["builds_today"] == 0
        assert "cat.sch.a" not in bs._build_locks

    def test_budget_resets_on_a_new_day(self):
        with patch.object(bs, "MAX_BUILDS_PER_DAY", 1), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.a", actor="a@x.com")
            bs._build_locks.clear()
            with pytest.raises(bs.BuildBudgetError):
                bs.submit_build_job("cat.sch.b", actor="a@x.com")
            bs._budget_day = "1999-01-01"
            bs.submit_build_job("cat.sch.b", actor="a@x.com")


class TestAttribution:
    def test_submitter_is_recorded(self):
        """Serverless spend needs a name attached to it."""
        with patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            bs.submit_build_job("cat.sch.orders", actor="admin@example.com")
        recent = bs.get_build_budget()["recent_submissions"]
        assert recent[0]["actor"] == "admin@example.com"
        assert recent[0]["table_fqn"] == "cat.sch.orders"
        assert recent[0]["at"]

    def test_submission_log_is_bounded(self):
        with patch.object(bs, "_BUILD_LOG_MAX", 10), \
             patch.object(bs, "MAX_BUILDS_PER_DAY", 10_000), \
             patch.object(bs, "MAX_BUILDS_IN_FLIGHT", 10_000), \
             patch("backend.build_service._get_client", return_value=_client()), \
             patch("backend.build_service.http_client.post", return_value=_ok_post()):
            for i in range(50):
                bs.submit_build_job(f"cat.sch.t{i}", actor="a@x.com")
                bs._build_locks.clear()
        assert len(bs._build_submitters) <= 10

    def test_status_shape(self):
        st = bs.get_build_budget()
        for k in ("day", "builds_today", "max_per_day", "remaining_today",
                  "in_flight", "max_in_flight", "recent_submissions"):
            assert k in st
