"""Tests for backend/build_service.py — covers A7, A12, and general build submission.

A7:  Path derivation no longer uses __file__; fails closed when PIPELINE_NOTEBOOK_PATH unset.
A12: Per-table build lock prevents concurrent duplicate Jobs for the same FQN.
"""
import os
import threading
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# A7 — Path derivation fail-closed
# ---------------------------------------------------------------------------

class TestPipelineNotebookPath:
    """A7: build_service must NOT derive broken container paths from __file__."""

    def test_path_from_env_is_used_directly(self):
        """When PIPELINE_NOTEBOOK_PATH is set, use it as-is."""
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/Users/me/notebooks/run_pipeline"}):
            # Re-import to pick up the env var
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            assert bs.PIPELINE_NOTEBOOK_PATH == "/Workspace/Users/me/notebooks/run_pipeline"

    def test_empty_when_env_unset(self):
        """When PIPELINE_NOTEBOOK_PATH is NOT set, return empty — fail closed."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            assert bs.PIPELINE_NOTEBOOK_PATH == ""

    def test_is_build_configured_false_when_empty(self):
        """is_build_configured() must return False when path is empty."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            assert bs.is_build_configured() is False

    def test_no_container_path_derivation(self):
        """A7 regression: must NOT produce paths like /Workspace/app/backend/..."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            # The old code would produce something like /Workspace/app/notebooks/run_pipeline
            assert not bs.PIPELINE_NOTEBOOK_PATH.startswith("/Workspace/app")
            assert "/app/" not in bs.PIPELINE_NOTEBOOK_PATH

    def test_submit_raises_when_not_configured(self):
        """submit_build_job raises RuntimeError when path is empty."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            with pytest.raises(RuntimeError, match="PIPELINE_NOTEBOOK_PATH is not configured"):
                bs.submit_build_job("catalog.schema.table")


# ---------------------------------------------------------------------------
# A12 — Per-table build lock
# ---------------------------------------------------------------------------

class TestPerTableBuildLock:
    """A12: Concurrent builds for the same table must be rejected."""

    @pytest.fixture(autouse=True)
    def _reload_build_service(self):
        """Ensure clean state for each test."""
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/test/nb"}):
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            # Clear any leftover locks
            bs._build_locks.clear()
            self.bs = bs
            yield

    def test_lock_registered_on_submit(self):
        """After successful submit, the FQN is registered in _build_locks."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": 12345}
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.build_service._get_client") as mock_client, \
             patch("backend.build_service.http_client.post", return_value=mock_resp):
            mock_client.return_value.config.host = "https://test.databricks.com"
            mock_client.return_value.config.authenticate.return_value = {"Authorization": "Bearer x"}

            run_id = self.bs.submit_build_job("cat.sch.tbl")
            assert run_id == "12345"
            assert "cat.sch.tbl" in self.bs._build_locks
            assert self.bs._build_locks["cat.sch.tbl"] == "12345"

    def test_concurrent_build_same_table_rejected(self):
        """Second build for same FQN raises RuntimeError while first is running."""
        # Simulate an existing in-progress build
        self.bs._build_locks["cat.sch.tbl"] = "99999"

        with pytest.raises(RuntimeError, match="already in progress"):
            self.bs.submit_build_job("cat.sch.tbl")

    def test_different_table_allowed_concurrently(self):
        """Builds for different tables are not blocked by each other."""
        self.bs._build_locks["cat.sch.table_a"] = "11111"

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": 22222}
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.build_service._get_client") as mock_client, \
             patch("backend.build_service.http_client.post", return_value=mock_resp):
            mock_client.return_value.config.host = "https://test.databricks.com"
            mock_client.return_value.config.authenticate.return_value = {"Authorization": "Bearer x"}

            # This should succeed — different table
            run_id = self.bs.submit_build_job("cat.sch.table_b")
            assert run_id == "22222"

    def test_lock_released_on_completion(self):
        """When get_build_status returns terminal state, lock is released."""
        from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState

        self.bs._build_locks["cat.sch.tbl"] = "12345"

        mock_run = MagicMock()
        mock_run.state.life_cycle_state = RunLifeCycleState.TERMINATED
        mock_run.state.result_state = RunResultState.SUCCESS
        mock_run.state.state_message = "Done"
        mock_run.run_page_url = "https://test.databricks.com/jobs/123"

        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = mock_run

            status = self.bs.get_build_status("12345")
            assert status.is_complete is True
            assert status.is_success is True
            # Lock should be released
            assert "cat.sch.tbl" not in self.bs._build_locks

    def test_lock_released_on_failure(self):
        """Lock is also released when build fails (INTERNAL_ERROR)."""
        from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState

        self.bs._build_locks["cat.sch.tbl"] = "12345"

        mock_run = MagicMock()
        mock_run.state.life_cycle_state = RunLifeCycleState.INTERNAL_ERROR
        mock_run.state.result_state = None
        mock_run.state.state_message = "Cluster crashed"
        mock_run.run_page_url = ""

        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = mock_run

            status = self.bs.get_build_status("12345")
            assert status.is_complete is True
            assert status.is_success is False
            assert "cat.sch.tbl" not in self.bs._build_locks


# ---------------------------------------------------------------------------
# General build submission tests
# ---------------------------------------------------------------------------

class TestBuildSubmission:
    """Basic tests for submit_build_job parameters and behavior."""

    @pytest.fixture(autouse=True)
    def _reload_build_service(self):
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/Users/test/notebooks/run_pipeline"}):
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            bs._build_locks.clear()
            self.bs = bs
            yield

    def test_submit_passes_correct_parameters(self):
        """Verify the job payload includes correct notebook path and parameters."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": 55555}
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.build_service._get_client") as mock_client, \
             patch("backend.build_service.http_client.post", return_value=mock_resp) as mock_post:
            mock_client.return_value.config.host = "https://host.databricks.com"
            mock_client.return_value.config.authenticate.return_value = {"Authorization": "Bearer tok"}

            self.bs.submit_build_job("main.gold.customers", "main", "gold", force_reparse=True)

            # Check the payload
            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
            task = payload["tasks"][0]
            params = task["notebook_task"]["base_parameters"]

            assert task["notebook_task"]["notebook_path"] == "/Workspace/Users/test/notebooks/run_pipeline"
            assert params["KPI_TABLES"] == "main.gold.customers"
            assert params["FORCE_REPARSE"] == "true"
            assert params["BUILD_ONLY"] == "true"

    def test_submit_url_uses_host(self):
        """API call goes to the correct host."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"run_id": 1}
        mock_resp.raise_for_status = MagicMock()

        with patch("backend.build_service._get_client") as mock_client, \
             patch("backend.build_service.http_client.post", return_value=mock_resp) as mock_post:
            mock_client.return_value.config.host = "https://myhost.cloud.databricks.com/"
            mock_client.return_value.config.authenticate.return_value = {}

            self.bs.submit_build_job("c.s.t")

            url = mock_post.call_args[0][0]
            assert url == "https://myhost.cloud.databricks.com/api/2.1/jobs/runs/submit"


# ---------------------------------------------------------------------------
# Build status polling
# ---------------------------------------------------------------------------

class TestBuildStatus:
    """Tests for get_build_status polling behavior."""

    @pytest.fixture(autouse=True)
    def _reload_build_service(self):
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/test/nb"}):
            import importlib
            import backend.build_service as bs
            importlib.reload(bs)
            bs._build_locks.clear()
            self.bs = bs
            yield

    def test_running_state_reports_progress(self):
        """RUNNING state should show ~50% progress."""
        from databricks.sdk.service.jobs import RunLifeCycleState

        mock_run = MagicMock()
        mock_run.state.life_cycle_state = RunLifeCycleState.RUNNING
        mock_run.state.result_state = None
        mock_run.state.state_message = "Running notebook"
        mock_run.run_page_url = "https://x.com/jobs/1/runs/2"

        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = mock_run

            status = self.bs.get_build_status("999")
            assert status.is_complete is False
            assert status.progress_pct == 50
            assert status.state == "RUNNING"

    def test_error_in_polling_returns_error_status(self):
        """If SDK call fails, return ERROR status (not crash)."""
        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.side_effect = Exception("Network timeout")

            status = self.bs.get_build_status("999")
            assert status.is_complete is True
            assert status.is_success is False
            assert status.state == "ERROR"
            assert "Network timeout" in status.state_message

    def test_steps_list_included(self):
        """Status always includes the build steps list for the progress UI."""
        from databricks.sdk.service.jobs import RunLifeCycleState

        mock_run = MagicMock()
        mock_run.state.life_cycle_state = RunLifeCycleState.PENDING
        mock_run.state.result_state = None
        mock_run.state.state_message = ""
        mock_run.run_page_url = ""

        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = mock_run

            status = self.bs.get_build_status("1")
            assert status.steps == self.bs.BUILD_STEPS
            assert status.total_steps == 8
