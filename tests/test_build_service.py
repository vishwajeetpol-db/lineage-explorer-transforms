"""Tests for backend/build_service.py — covers A7, A12, and general build submission.

A7:  Path derivation uses get_pipeline_notebook_path() with env-first + App source
     discovery fallback. Never derives from __file__.
A12: Per-table build lock prevents concurrent duplicate Jobs for the same FQN.
"""
import os
import threading
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# A7 — Path derivation via get_pipeline_notebook_path()
# ---------------------------------------------------------------------------

class TestPipelineNotebookPath:
    """A7: build_service must NOT derive broken container paths from __file__.
    Uses get_pipeline_notebook_path() with lazy cache."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        """Reset the lazy cache before each test."""
        import backend.build_service as bs
        bs._reset_pipeline_notebook_path_cache()
        yield
        bs._reset_pipeline_notebook_path_cache()

    def test_path_from_env_is_used_directly(self):
        """When PIPELINE_NOTEBOOK_PATH is set, use it as-is."""
        import backend.build_service as bs
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/Users/me/notebooks/run_pipeline"}):
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == "/Workspace/Users/me/notebooks/run_pipeline"

    def test_empty_when_env_unset_and_no_app(self):
        """When PIPELINE_NOTEBOOK_PATH is NOT set and no DATABRICKS_APP_NAME, return empty."""
        import backend.build_service as bs
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            os.environ.pop("DATABRICKS_APP_NAME", None)
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == ""

    def test_whitespace_only_env_treated_as_unset(self):
        """Whitespace-only PIPELINE_NOTEBOOK_PATH is treated as unset."""
        import backend.build_service as bs
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "   \t  "}, clear=False):
            os.environ.pop("DATABRICKS_APP_NAME", None)
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == ""

    def test_is_build_configured_false_when_empty(self):
        """is_build_configured() must return False when path is empty."""
        import backend.build_service as bs
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            os.environ.pop("DATABRICKS_APP_NAME", None)
            bs._reset_pipeline_notebook_path_cache()
            assert bs.is_build_configured() is False

    def test_no_container_path_derivation(self):
        """A7 regression: must NOT produce paths like /Workspace/app/backend/..."""
        import backend.build_service as bs
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            os.environ.pop("DATABRICKS_APP_NAME", None)
            bs._reset_pipeline_notebook_path_cache()
            path = bs.get_pipeline_notebook_path()
            assert not path.startswith("/Workspace/app")
            assert "/app/" not in path

    def test_submit_raises_when_not_configured(self):
        """submit_build_job raises RuntimeError when path is empty."""
        import backend.build_service as bs
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            os.environ.pop("DATABRICKS_APP_NAME", None)
            bs._reset_pipeline_notebook_path_cache()
            with pytest.raises(RuntimeError, match="PIPELINE_NOTEBOOK_PATH is not configured"):
                bs.submit_build_job("catalog.schema.table")

    def test_cache_is_lazy_and_reused(self):
        """Once resolved, get_pipeline_notebook_path() returns cached value without re-reading env."""
        import backend.build_service as bs
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/first"}):
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == "/Workspace/first"
        # Even after env changes, cache retains old value
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/second"}):
            assert bs.get_pipeline_notebook_path() == "/Workspace/first"
        # Reset cache picks up new value
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/second"}):
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == "/Workspace/second"


# ---------------------------------------------------------------------------
# A7 — App source discovery fallback
# ---------------------------------------------------------------------------

class TestAppSourceDiscovery:
    """A7: When env is unset but DATABRICKS_APP_NAME is set, discover from App source."""

    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        import backend.build_service as bs
        bs._reset_pipeline_notebook_path_cache()
        yield
        bs._reset_pipeline_notebook_path_cache()

    def test_discover_from_default_source_code_path(self):
        """Discovers notebook path from app_info.default_source_code_path."""
        import backend.build_service as bs

        mock_app = MagicMock()
        mock_app.default_source_code_path = "/Workspace/Users/deploy@db.com/bricktrace"
        mock_app.source_code_path = None
        mock_app.active_deployment = None
        mock_app.pending_deployment = None

        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "bricktrace-dev"}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            with patch("backend.build_service._get_client") as mock_client:
                mock_client.return_value.apps.get.return_value = mock_app
                bs._reset_pipeline_notebook_path_cache()
                result = bs.get_pipeline_notebook_path()
                assert result == "/Workspace/Users/deploy@db.com/bricktrace/notebooks/run_pipeline"

    def test_discover_from_active_deployment(self):
        """Falls back to active_deployment.source_code_path."""
        import backend.build_service as bs

        mock_deploy = MagicMock()
        mock_deploy.source_code_path = "/Workspace/Shared/apps/bricktrace"

        mock_app = MagicMock()
        mock_app.default_source_code_path = None
        mock_app.source_code_path = None
        mock_app.active_deployment = mock_deploy
        mock_app.pending_deployment = None

        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "bt"}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            with patch("backend.build_service._get_client") as mock_client:
                mock_client.return_value.apps.get.return_value = mock_app
                bs._reset_pipeline_notebook_path_cache()
                result = bs.get_pipeline_notebook_path()
                assert result == "/Workspace/Shared/apps/bricktrace/notebooks/run_pipeline"

    def test_users_prefix_normalized_to_workspace(self):
        """/Users/... prefix is normalized to /Workspace/Users/..."""
        import backend.build_service as bs

        mock_app = MagicMock()
        mock_app.default_source_code_path = "/Users/me@db.com/bricktrace"
        mock_app.source_code_path = None
        mock_app.active_deployment = None
        mock_app.pending_deployment = None

        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "bt"}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            with patch("backend.build_service._get_client") as mock_client:
                mock_client.return_value.apps.get.return_value = mock_app
                bs._reset_pipeline_notebook_path_cache()
                result = bs.get_pipeline_notebook_path()
                assert result == "/Workspace/Users/me@db.com/bricktrace/notebooks/run_pipeline"

    def test_shared_prefix_normalized_to_workspace(self):
        """/Shared/... prefix is normalized to /Workspace/Shared/..."""
        import backend.build_service as bs

        mock_app = MagicMock()
        mock_app.default_source_code_path = "/Shared/team/bricktrace"
        mock_app.source_code_path = None
        mock_app.active_deployment = None
        mock_app.pending_deployment = None

        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "bt"}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            with patch("backend.build_service._get_client") as mock_client:
                mock_client.return_value.apps.get.return_value = mock_app
                bs._reset_pipeline_notebook_path_cache()
                result = bs.get_pipeline_notebook_path()
                assert result == "/Workspace/Shared/team/bricktrace/notebooks/run_pipeline"

    def test_discovery_failure_returns_empty(self):
        """If apps.get raises, return empty (fail closed, non-fatal)."""
        import backend.build_service as bs

        with patch.dict(os.environ, {"DATABRICKS_APP_NAME": "bt"}, clear=False):
            os.environ.pop("PIPELINE_NOTEBOOK_PATH", None)
            with patch("backend.build_service._get_client") as mock_client:
                mock_client.return_value.apps.get.side_effect = Exception("API error")
                bs._reset_pipeline_notebook_path_cache()
                assert bs.get_pipeline_notebook_path() == ""

    def test_env_takes_precedence_over_app_discovery(self):
        """Env var wins even when DATABRICKS_APP_NAME is set."""
        import backend.build_service as bs

        with patch.dict(os.environ, {
            "PIPELINE_NOTEBOOK_PATH": "/Workspace/explicit/path",
            "DATABRICKS_APP_NAME": "bt",
        }, clear=False):
            bs._reset_pipeline_notebook_path_cache()
            assert bs.get_pipeline_notebook_path() == "/Workspace/explicit/path"


# ---------------------------------------------------------------------------
# A12 — Per-table build lock
# ---------------------------------------------------------------------------

class TestPerTableBuildLock:
    """A12: Concurrent builds for the same table must be rejected."""

    @pytest.fixture(autouse=True)
    def _reload_build_service(self):
        """Ensure clean state for each test."""
        import backend.build_service as bs
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": "/Workspace/test/nb"}):
            bs._reset_pipeline_notebook_path_cache()
            # Clear any leftover locks
            bs._build_locks.clear()
            self.bs = bs
            yield
            bs._reset_pipeline_notebook_path_cache()

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
