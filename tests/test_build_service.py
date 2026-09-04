"""Tests for backend/build_service.py — covers A7, A12, and general build submission.

A7:  Path derivation uses get_pipeline_notebook_path() with env-first + App source
     discovery fallback. Never derives from __file__.
A12: Per-table build lock prevents concurrent duplicate Jobs for the same FQN.
"""
import os
import threading
import time
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


# ---------------------------------------------------------------------------
# Build source reachability
#
# `bundle deploy` leaves the source in the DEPLOYER's home folder, which the
# app's service principal has no permission on — so the build job it submits
# cannot open notebooks/run_pipeline. Preflight must catch that for free, say
# how to fix it, and never latch the failure (a grant applied while the app is
# running has to take effect without a restart).
# ---------------------------------------------------------------------------

class TestBuildSourceAccess:
    """Preflight: fail fast, name the fix, latch success only."""

    NB = "/Workspace/Users/deployer/.bundle/bricktrace/dev/files/notebooks/run_pipeline"

    @pytest.fixture(autouse=True)
    def _clean(self):
        import backend.build_service as bs
        with patch.dict(os.environ, {"PIPELINE_NOTEBOOK_PATH": self.NB}):
            bs._reset_pipeline_notebook_path_cache()
            bs._reset_source_access_cache()
            bs._build_locks.clear()
            self.bs = bs
            yield
            bs._reset_pipeline_notebook_path_cache()
            bs._reset_source_access_cache()
            bs._build_locks.clear()

    @staticmethod
    def _client():
        client = MagicMock()
        client.config.host = "https://test.databricks.com"
        client.config.authenticate.return_value = {"Authorization": "Bearer x"}
        return client

    @staticmethod
    def _ok_post():
        resp = MagicMock()
        resp.json.return_value = {"run_id": 4242}
        resp.raise_for_status = MagicMock()
        return resp

    def test_unreachable_notebook_blocks_submit(self):
        """No serverless run is paid for, and no build lock is taken."""
        client = self._client()
        client.workspace.get_status.side_effect = Exception("RESOURCE_DOES_NOT_EXIST")

        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post") as mock_post:
            with pytest.raises(self.bs.BuildSourceAccessError) as exc:
                self.bs.submit_build_job("cat.sch.tbl")

        mock_post.assert_not_called()
        assert "cat.sch.tbl" not in self.bs._build_locks
        # Must name the remediation, not just restate the symptom.
        assert "grant_build_source_access.sh" in str(exc.value)

    def test_reachable_notebook_submits(self):
        """A readable notebook path submits exactly as before."""
        client = self._client()
        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", return_value=self._ok_post()):
            assert self.bs.submit_build_job("cat.sch.tbl") == "4242"
        client.workspace.get_status.assert_called_once_with(self.NB)

    def test_failure_is_not_latched(self):
        """After the grant lands, the next build works without an app restart."""
        client = self._client()
        client.workspace.get_status.side_effect = Exception("PERMISSION_DENIED")

        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", return_value=self._ok_post()):
            with pytest.raises(self.bs.BuildSourceAccessError):
                self.bs.submit_build_job("cat.sch.tbl")

            # Operator runs grant_build_source_access.sh — same process, no restart.
            client.workspace.get_status.side_effect = None
            assert self.bs.submit_build_job("cat.sch.tbl") == "4242"

    def test_success_is_latched(self):
        """A proven-reachable path is not re-probed on every build."""
        client = self._client()
        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", return_value=self._ok_post()):
            self.bs.submit_build_job("cat.sch.a")
            self.bs._build_locks.clear()
            self.bs.submit_build_job("cat.sch.b")
        assert client.workspace.get_status.call_count == 1

    def test_latch_expires_so_a_revoked_grant_is_noticed(self):
        """The positive latch must not outlive the grant it proved.

        A permanent latch means a grant revoked under a long-lived app is never
        seen again: every later build sails past the preflight and dies a minute in
        on serverless compute, which is the cost the preflight exists to avoid.
        """
        client = self._client()
        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", return_value=self._ok_post()):
            self.bs.submit_build_job("cat.sch.a")
            assert client.workspace.get_status.call_count == 1

            # The grant is revoked, and the latch's TTL lapses.
            client.workspace.get_status.side_effect = Exception("PERMISSION_DENIED")
            with patch(
                "backend.build_service.time.monotonic",
                return_value=time.monotonic() + self.bs._SOURCE_ACCESS_TTL_SECONDS + 1,
            ):
                self.bs._build_locks.clear()
                with pytest.raises(self.bs.BuildSourceAccessError):
                    self.bs.submit_build_job("cat.sch.b")

        assert client.workspace.get_status.call_count == 2

    def test_slot_is_reserved_before_the_preflight(self):
        """A second request for the same table is rejected while the first submits.

        The lock used to be read at the top and written only after runs/submit
        returned, so two requests could both clear the check and both bill a
        serverless run. The reservation has to be visible during the preflight.
        """
        client = self._client()
        seen: list[str | None] = []

        def _probe(_path):
            # Runs inside the window that used to be unguarded.
            seen.append(self.bs._build_locks.get("cat.sch.tbl"))

        client.workspace.get_status.side_effect = _probe
        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", return_value=self._ok_post()):
            self.bs.submit_build_job("cat.sch.tbl")

        assert seen == [self.bs._BUILD_RESERVED]
        assert self.bs._build_locks["cat.sch.tbl"] == "4242"

    def test_failed_submit_releases_the_reservation(self):
        """A submit that raises must not leave the table locked out until restart."""
        client = self._client()
        with patch("backend.build_service._get_client", return_value=client), \
             patch("backend.build_service.http_client.post", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                self.bs.submit_build_job("cat.sch.tbl")

        assert "cat.sch.tbl" not in self.bs._build_locks

    def test_reservation_rejects_a_concurrent_request(self):
        """The reservation is honoured by the duplicate-build check."""
        self.bs._build_locks["cat.sch.tbl"] = self.bs._BUILD_RESERVED
        with pytest.raises(RuntimeError, match="being submitted right now"):
            self.bs.submit_build_job("cat.sch.tbl")


class TestSourceAccessFailureMessage:
    """A build that already failed this way must explain itself in the panel."""

    # Verbatim from a real failed run (bricktrace-dev, 2026-08-22).
    REAL = (
        'Task build_lineage failed with message: Unable to access the notebook '
        '"/Workspace/Users/3e737620-bbd8-44a1-85bb-3d80c26327b5/.bundle/bricktrace/dev/files/'
        'notebooks/run_pipeline" in the workspace. Either it does not exist, or the identity '
        'used to run this job, app-1bf12b bricktrace-dev (50d7a56f-0016-487b-9cbe-bbbe8e1ebee3), '
        'lacks the required permissions.'
    )

    @pytest.fixture(autouse=True)
    def _bs(self):
        import backend.build_service as bs
        bs._build_locks.clear()
        bs._reset_source_access_cache()
        self.bs = bs
        yield
        bs._build_locks.clear()
        bs._reset_source_access_cache()

    def test_matcher_recognizes_the_real_message(self):
        assert self.bs._is_source_access_failure(self.REAL) is True

    def test_matcher_ignores_unrelated_failures(self):
        assert self.bs._is_source_access_failure("AnalysisException: TABLE_OR_VIEW_NOT_FOUND") is False
        assert self.bs._is_source_access_failure("") is False
        assert self.bs._is_source_access_failure(None) is False

    def test_matcher_ignores_other_notebook_permission_failures(self):
        """A different notebook-permission failure must not get this remediation.

        The hint names one cause and one fix (CAN_RUN on the deployed source folder,
        via grant_build_source_access.sh). Any notebook-permission failure that
        merely mentions both "notebook" and "lacks the required permissions" used to
        match, so the panel confidently stated the wrong cause and the wrong fix.
        """
        assert self.bs._is_source_access_failure(
            "the identity lacks the required permissions on notebook /Shared/other"
        ) is False
        assert self.bs._is_source_access_failure(
            "Run failed: user lacks the required permissions to attach the notebook"
        ) is False
        # ...while the platform's real message, which names the run identity, still does.
        assert self.bs._is_source_access_failure(
            "the identity used to run this job, app-x (sp-1), lacks the required permissions."
        ) is True

    def _failed_run(self, message):
        from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState
        run = MagicMock()
        run.state.life_cycle_state = RunLifeCycleState.TERMINATED
        run.state.result_state = RunResultState.FAILED
        run.state.state_message = message
        run.run_page_url = ""
        return run

    def test_hint_appended_to_failed_status(self):
        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = self._failed_run(self.REAL)
            status = self.bs.get_build_status("777")

        assert status.is_success is False
        assert self.REAL in status.state_message          # platform detail preserved
        assert "grant_build_source_access.sh" in status.state_message

    def test_unrelated_failure_message_untouched(self):
        msg = "Task build_lineage failed with message: ZeroDivisionError"
        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = self._failed_run(msg)
            status = self.bs.get_build_status("778")
        assert status.state_message == msg

    def test_successful_run_message_untouched(self):
        from databricks.sdk.service.jobs import RunLifeCycleState, RunResultState
        run = MagicMock()
        run.state.life_cycle_state = RunLifeCycleState.TERMINATED
        run.state.result_state = RunResultState.SUCCESS
        run.state.state_message = ""
        run.run_page_url = ""
        with patch("backend.build_service._get_client") as mock_client:
            mock_client.return_value.jobs.get_run.return_value = run
            status = self.bs.get_build_status("779")
        assert status.is_success is True
        assert status.state_message == ""
