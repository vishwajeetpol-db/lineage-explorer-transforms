"""Full-coverage tests for backend.server.producer_source.

Complements tests/test_producer_source.py (which covers _is_access_error,
_FetchDiag, _fetch_pipeline_source library shapes, analyze_producer reason_code,
and compare_producers). This file targets the CURRENTLY-UNCOVERED fetchers,
the stored/fresh analyze_producer paths, the unified resolver, and the
cross-source version listing + comparison helpers.

All mocking is offline: no Databricks SDK calls, no network, no warehouse.
"""
import base64
from unittest.mock import MagicMock, patch

import pytest

from backend.server import producer_source as ps


# ---------------------------------------------------------------------------
# _fetch_notebook_source
# ---------------------------------------------------------------------------

class TestFetchNotebookSource:
    def test_success_base64_decode(self):
        payload = base64.b64encode(b"print('hello nb')").decode("ascii")
        client = MagicMock()
        client.workspace.export.return_value = MagicMock(content=payload)
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_notebook_source("/W/nb")
        assert src == "print('hello nb')"
        # ExportFormat.SOURCE enum used (not bare string)
        _, kwargs = client.workspace.export.call_args
        assert kwargs["path"] == "/W/nb"

    def test_export_error_returns_empty_and_notes_diag(self):
        client = MagicMock()
        client.workspace.export.side_effect = RuntimeError("PERMISSION_DENIED on nb")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_notebook_source("/W/secret", diag=diag)
        assert src == ""
        assert "/W/secret" in diag.denied_paths


# ---------------------------------------------------------------------------
# _fetch_workspace_file
# ---------------------------------------------------------------------------

class TestFetchWorkspaceFile:
    def test_download_success_bytes_decode(self):
        resp = MagicMock()
        resp.read.return_value = b"df = spark.read.table('x')"
        client = MagicMock()
        client.workspace.download.return_value = resp
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_workspace_file("/W/etl.py")
        assert src == "df = spark.read.table('x')"

    def test_download_returns_plain_str(self):
        # resp has no .read() -> data used directly, str branch
        client = MagicMock()
        client.workspace.download.return_value = "SELECT 1"
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_workspace_file("/W/q.sql")
        assert src == "SELECT 1"

    def test_download_error_falls_back_to_notebook_source(self):
        client = MagicMock()
        client.workspace.download.side_effect = RuntimeError("not a file, use export")
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="NB FALLBACK") as mock_nb:
            src = ps._fetch_workspace_file("/W/thing")
        assert src == "NB FALLBACK"
        mock_nb.assert_called_once()

    def test_access_error_records_diag_then_falls_back(self):
        client = MagicMock()
        client.workspace.download.side_effect = RuntimeError("403 Forbidden")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="") as mock_nb:
            src = ps._fetch_workspace_file("/W/denied", diag=diag)
        assert src == ""
        assert "/W/denied" in diag.denied_paths
        mock_nb.assert_called_once()


# ---------------------------------------------------------------------------
# _fetch_query_source
# ---------------------------------------------------------------------------

class TestFetchQuerySource:
    def test_query_returned(self):
        client = MagicMock()
        client.queries.get.return_value = MagicMock(query="SELECT a, b FROM t")
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_query_source("q-1")
        assert src == "SELECT a, b FROM t"

    def test_error_returns_empty_and_notes_diag(self):
        client = MagicMock()
        client.queries.get.side_effect = RuntimeError("PERMISSION_DENIED")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_query_source("q-1", diag=diag)
        assert src == ""
        assert "query:q-1" in diag.denied_paths


# ---------------------------------------------------------------------------
# _fetch_job_source
# ---------------------------------------------------------------------------

class TestFetchJobSource:
    def _job_with_tasks(self, tasks):
        job = MagicMock()
        job.settings.tasks = tasks
        return job

    def test_notebook_task_delegates_to_notebook_source(self):
        nb_task = MagicMock()
        nb_task.notebook_task = MagicMock(notebook_path="/W/job_nb")
        client = MagicMock()
        client.jobs.get.return_value = self._job_with_tasks([nb_task])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="JOB NB SRC") as mock_nb:
            src = ps._fetch_job_source("42")
        assert src == "JOB NB SRC"
        mock_nb.assert_called_once_with("/W/job_nb", diag=None)

    def test_no_notebook_task_returns_empty(self):
        other = MagicMock()
        other.notebook_task = None
        client = MagicMock()
        client.jobs.get.return_value = self._job_with_tasks([other])
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_job_source("42")
        assert src == ""

    def test_error_returns_empty_and_notes_diag(self):
        client = MagicMock()
        client.jobs.get.side_effect = RuntimeError("job was not found")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_job_source("42", diag=diag)
        assert src == ""
        assert diag.entity_missing is True


# ---------------------------------------------------------------------------
# _list_workspace_source_files
# ---------------------------------------------------------------------------

class TestListWorkspaceSourceFiles:
    def _obj(self, otype, path):
        o = MagicMock()
        o.object_type = MagicMock(value=otype)
        o.path = path
        return o

    def test_walks_directory_recursion_notebook_and_matching_file(self):
        def list_side_effect(path):
            if path == "/root":
                return [
                    self._obj("DIRECTORY", "/root/sub"),
                    self._obj("NOTEBOOK", "/root/nb1"),
                    self._obj("FILE", "/root/keep.py"),
                    self._obj("FILE", "/root/skip.txt"),  # non-matching ext
                ]
            if path == "/root/sub":
                return [self._obj("FILE", "/root/sub/inner.sql")]
            return []

        client = MagicMock()
        client.workspace.list.side_effect = list_side_effect
        with patch.object(ps, "_get_client", return_value=client):
            found = ps._list_workspace_source_files("/root")
        assert "/root/nb1" in found
        assert "/root/keep.py" in found
        assert "/root/sub/inner.sql" in found
        assert "/root/skip.txt" not in found

    def test_list_error_records_diag_and_continues(self):
        client = MagicMock()
        client.workspace.list.side_effect = RuntimeError("403 denied listing")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client):
            found = ps._list_workspace_source_files("/root", diag=diag)
        assert found == []
        assert "/root" in diag.denied_paths


# ---------------------------------------------------------------------------
# _current_source_hash
# ---------------------------------------------------------------------------

class TestCurrentSourceHash:
    def test_returns_hash_when_source_present(self):
        with patch.object(ps, "_fetch_source", return_value="some code"), \
             patch.object(ps.analysis_store, "_source_hash", return_value="deadbeef"):
            assert ps._current_source_hash("NOTEBOOK", "/W/nb") == "deadbeef"

    def test_none_when_source_empty(self):
        with patch.object(ps, "_fetch_source", return_value="   "):
            assert ps._current_source_hash("NOTEBOOK", "/W/nb") is None

    def test_none_on_error(self):
        with patch.object(ps, "_fetch_source", side_effect=RuntimeError("boom")):
            assert ps._current_source_hash("NOTEBOOK", "/W/nb") is None


# ---------------------------------------------------------------------------
# analyze_producer — stored + fresh + no-analysis paths
# ---------------------------------------------------------------------------

class TestAnalyzeProducerStoredAndFresh:
    def test_stored_version_load_path_with_stale_flag(self):
        latest = {
            "columns": [{"target_column": "a"}],
            "source_hash": "OLDHASH",
            "llm_model": "m1",
            "version": 3,
            "analyzed_at": "2026-01-01",
            "analyzed_by": "someone",
        }
        with patch.object(ps.analysis_store, "get_latest_version", return_value=latest), \
             patch.object(ps.analysis_store, "list_versions", return_value=[{"version": 3}]), \
             patch.object(ps, "_current_source_hash", return_value="NEWHASH"):
            out = ps.analyze_producer("JOB", "1", "c.s.t")
        assert out["source"] == "stored"
        assert out["version"] == 3
        assert out["columns"] == [{"target_column": "a"}]
        assert out["stale"] is True
        assert out["versions"] == [{"version": 3}]

    def test_stored_not_stale_when_hash_matches(self):
        latest = {
            "columns": [], "source_hash": "SAME", "llm_model": "m",
            "version": 1, "analyzed_at": "", "analyzed_by": "",
        }
        with patch.object(ps.analysis_store, "get_latest_version", return_value=latest), \
             patch.object(ps.analysis_store, "list_versions", return_value=[]), \
             patch.object(ps, "_current_source_hash", return_value="SAME"):
            out = ps.analyze_producer("JOB", "1", "c.s.t")
        assert out["source"] == "stored"
        assert out["stale"] is False

    def test_fresh_llm_success_path(self):
        cols = [{"target_column": "amount_usd", "expression": "amount * fx"}]
        with patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps, "_fetch_source", return_value="df = ..."), \
             patch.object(ps.analysis_store, "_source_hash", return_value="H1"), \
             patch.object(ps, "_fetch_target_columns", return_value=["amount_usd"]), \
             patch.object(ps.llm_client, "LLM_MODEL_NAME", "default-model"), \
             patch.object(ps.llm_client, "analyze_source_code", return_value=cols), \
             patch.object(ps.analysis_store, "save_analysis", return_value=7), \
             patch.object(ps.analysis_store, "list_versions", return_value=[{"version": 7}]):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["source"] == "llm"
        assert out["columns"] == cols
        assert out["version"] == 7
        assert out["llm_model"] == "default-model"
        assert out["source_hash"] == "H1"
        assert out["stale"] is False

    def test_fresh_llm_uses_explicit_model_and_given_target_columns(self):
        with patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps, "_fetch_source", return_value="code"), \
             patch.object(ps.analysis_store, "_source_hash", return_value="H"), \
             patch.object(ps.llm_client, "analyze_source_code",
                          return_value=[{"target_column": "x", "source_columns": ["a"],
                                         "expression": "a", "category": "PASS_THROUGH"}]) as mock_llm, \
             patch.object(ps.analysis_store, "save_analysis", return_value=1), \
             patch.object(ps.analysis_store, "list_versions", return_value=[]), \
             patch.object(ps, "_fetch_target_columns") as mock_tc:
            out = ps.analyze_producer(
                "JOB", "1", "c.s.t", force_rerun=True,
                target_columns=["x"], model="custom-model",
            )
        assert out["llm_model"] == "custom-model"
        # target_columns supplied -> _fetch_target_columns NOT called
        mock_tc.assert_not_called()
        assert mock_llm.call_args.kwargs["model"] == "custom-model"

    def test_llm_returned_no_analysis(self):
        with patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps, "_fetch_source", return_value="code"), \
             patch.object(ps.analysis_store, "_source_hash", return_value="H"), \
             patch.object(ps, "_fetch_target_columns", return_value=[]), \
             patch.object(ps.llm_client, "LLM_MODEL_NAME", "m"), \
             patch.object(ps.llm_client, "analyze_source_code", return_value=[]):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        # Empty/all-UNKNOWN analysis now signals a metadata-driven framework and
        # points the user at deep framework analysis (not a generic failure).
        assert out["source"] == "unavailable"
        assert out["reason_code"] == "no_columns"
        assert "metadata-driven framework" in out["detail"]

    def test_save_analysis_error_still_returns_llm_result(self):
        with patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps, "_fetch_source", return_value="code"), \
             patch.object(ps.analysis_store, "_source_hash", return_value="H"), \
             patch.object(ps, "_fetch_target_columns", return_value=[]), \
             patch.object(ps.llm_client, "LLM_MODEL_NAME", "m"), \
             patch.object(ps.llm_client, "analyze_source_code",
                          return_value=[{"target_column": "x", "source_columns": ["a"],
                                         "expression": "a", "category": "PASS_THROUGH"}]), \
             patch.object(ps.analysis_store, "save_analysis", side_effect=RuntimeError("write failed")), \
             patch.object(ps.analysis_store, "list_versions", return_value=[]):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["source"] == "llm"
        assert out["version"] == 1  # falls back to default

    def test_llm_not_configured(self):
        with patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps.llm_client, "is_llm_configured", return_value=False):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["source"] == "unavailable"
        assert "LLM not configured" in out["detail"]


# ---------------------------------------------------------------------------
# resolve_column_transformations
# ---------------------------------------------------------------------------

class TestResolveColumnTransformations:
    def test_captured_plan_hit(self):
        cap = {
            "version": 4, "captured_via": "spark", "captured_at": "2026-01-01",
            "columns": [{"target_column": "a", "source_columns": ["a"], "expression": "a"}],
        }
        with patch("backend.plan_capture_service.get_captured_columns", return_value=cap):
            out = ps.resolve_column_transformations("c", "s", "t")
        assert out["source"] == "plan_capture"
        assert out["version"] == 4
        assert "Captured Spark plan" in out["source_label"]
        assert out["columns"][0]["target_column"] == "a"

    def test_cdc_spec_hit(self):
        spec = {"version": 2, "keys": ["id"], "scd_type": 2, "captured_at": "2026-01-02"}
        with patch("backend.plan_capture_service.get_captured_columns", return_value=None), \
             patch("backend.plan_capture_service.get_captured_cdc_spec", return_value=spec):
            out = ps.resolve_column_transformations("c", "s", "t")
        assert out["source"] == "cdc_spec"
        assert out["cdc_spec"] == spec
        assert out["version"] == 2
        assert "AUTO CDC spec" in out["source_label"]

    def test_no_entity_none_branch(self):
        with patch("backend.plan_capture_service.get_captured_columns", return_value=None), \
             patch("backend.plan_capture_service.get_captured_cdc_spec", return_value=None):
            out = ps.resolve_column_transformations("c", "s", "t")
        assert out["source"] == "none"
        assert "Pick a producer entity" in out["detail"]

    def test_stored_llm_via_analyze_producer(self):
        llm_out = {
            "source": "stored", "columns": [{"target_column": "x"}], "version": 3,
            "versions": [{"version": 3}], "stale": True, "llm_model": "m",
            "analyzed_at": "t", "detail": None, "reason_code": None,
            "denied_paths": None, "app_service_principal": None,
        }
        with patch("backend.plan_capture_service.get_captured_columns", return_value=None), \
             patch("backend.plan_capture_service.get_captured_cdc_spec", return_value=None), \
             patch.object(ps, "analyze_producer", return_value=llm_out):
            out = ps.resolve_column_transformations(
                "c", "s", "t", entity_type="JOB", entity_id="1")
        assert out["source"] == "stored"
        assert "Stored LLM analysis" in out["source_label"]
        assert out["version"] == 3
        assert out["stale"] is True

    def test_fresh_llm_via_force_rerun_skips_offline(self):
        llm_out = {
            "source": "llm", "columns": [], "version": 1, "versions": [],
            "stale": False, "llm_model": "m", "analyzed_at": "t",
        }
        with patch("backend.plan_capture_service.get_captured_columns") as mock_cap, \
             patch.object(ps, "analyze_producer", return_value=llm_out):
            out = ps.resolve_column_transformations(
                "c", "s", "t", entity_type="JOB", entity_id="1", force_rerun=True)
        assert out["source"] == "llm"
        assert "Fresh LLM analysis" in out["source_label"]
        # offline captured-plan path skipped when force_rerun
        mock_cap.assert_not_called()

    def test_captured_columns_raises_falls_through_to_cdc(self):
        with patch("backend.plan_capture_service.get_captured_columns", side_effect=RuntimeError("x")), \
             patch("backend.plan_capture_service.get_captured_cdc_spec", side_effect=RuntimeError("y")):
            out = ps.resolve_column_transformations("c", "s", "t")
        assert out["source"] == "none"


# ---------------------------------------------------------------------------
# list_all_versions
# ---------------------------------------------------------------------------

class TestListAllVersions:
    def test_merges_plan_capture_and_llm_versions(self):
        plan_versions = [{"ref": "plan_capture:2", "source": "plan_capture", "version": 2}]
        llm_versions = [
            {"version": 5, "llm_model": "gpt", "analyzed_at": "t", "analyzed_by": "u"},
        ]
        with patch("backend.plan_capture_service.list_captured_versions", return_value=plan_versions), \
             patch.object(ps.analysis_store, "list_versions", return_value=llm_versions):
            out = ps.list_all_versions("c", "s", "t", entity_type="JOB", entity_id="1")
        refs = [v["ref"] for v in out]
        assert "plan_capture:2" in refs
        assert "llm:5" in refs
        llm_row = next(v for v in out if v["ref"] == "llm:5")
        assert llm_row["label"] == "LLM v5 (gpt)"

    def test_no_entity_only_plan_versions(self):
        with patch("backend.plan_capture_service.list_captured_versions",
                   return_value=[{"ref": "plan_capture:1"}]):
            out = ps.list_all_versions("c", "s", "t")
        assert out == [{"ref": "plan_capture:1"}]

    def test_plan_list_error_swallowed(self):
        with patch("backend.plan_capture_service.list_captured_versions", side_effect=RuntimeError("x")), \
             patch.object(ps.analysis_store, "list_versions", side_effect=RuntimeError("y")):
            out = ps.list_all_versions("c", "s", "t", entity_type="JOB", entity_id="1")
        assert out == []


# ---------------------------------------------------------------------------
# _columns_for_ref
# ---------------------------------------------------------------------------

class TestColumnsForRef:
    def test_plan_capture_ref(self):
        cap = {"version": 3, "captured_at": "t",
               "columns": [{"target_column": "a", "source_columns": ["a"], "expression": "a"}]}
        with patch("backend.plan_capture_service.get_captured_columns_version", return_value=cap):
            out = ps._columns_for_ref("c", "s", "t", "plan_capture:3", None, None)
        assert out["source"] == "plan_capture"
        assert out["version"] == 3
        assert out["columns"][0]["target_column"] == "a"

    def test_plan_capture_ref_missing(self):
        with patch("backend.plan_capture_service.get_captured_columns_version", return_value=None):
            out = ps._columns_for_ref("c", "s", "t", "plan_capture:9", None, None)
        assert out is None

    def test_llm_ref(self):
        row = {"version": 5, "columns": [{"target_column": "x"}], "analyzed_at": "t", "source_hash": "H"}
        with patch.object(ps.analysis_store, "get_version", return_value=row):
            out = ps._columns_for_ref("c", "s", "t", "llm:5", "JOB", "1")
        assert out["source"] == "llm"
        assert out["version"] == 5
        assert out["source_hash"] == "H"

    def test_llm_ref_missing_row(self):
        with patch.object(ps.analysis_store, "get_version", return_value=None):
            out = ps._columns_for_ref("c", "s", "t", "llm:5", "JOB", "1")
        assert out is None

    def test_bad_ref_returns_none(self):
        assert ps._columns_for_ref("c", "s", "t", "not-a-ref", None, None) is None

    def test_llm_ref_without_entity_returns_none(self):
        assert ps._columns_for_ref("c", "s", "t", "llm:1", None, None) is None


# ---------------------------------------------------------------------------
# compare_transformation_versions
# ---------------------------------------------------------------------------

class TestCompareTransformationVersions:
    def _ref(self, ref, cols):
        return {
            "ref": ref, "source": "llm", "version": int(ref.split(":")[1]),
            "label": f"LLM {ref}", "analyzed_at": "t", "columns": cols,
        }

    def test_two_llm_refs_diff(self):
        a = self._ref("llm:1", [
            {"target_column": "id", "source_columns": ["id"], "expression": "id"},
            {"target_column": "amt", "source_columns": ["a"], "expression": "a*1"},
            {"target_column": "gone", "source_columns": ["g"], "expression": "g"},
        ])
        b = self._ref("llm:2", [
            {"target_column": "id", "source_columns": ["id"], "expression": "id"},
            {"target_column": "amt", "source_columns": ["a"], "expression": "a*2"},  # changed
            {"target_column": "added", "source_columns": ["n"], "expression": "n"},  # added
        ])
        with patch.object(ps, "_columns_for_ref", side_effect=[a, b]):
            out = ps.compare_transformation_versions("c", "s", "t", "llm:1", "llm:2", "JOB", "1")
        by_col = {d["column"]: d for d in out["column_diffs"]}
        assert by_col["id"]["status"] == "unchanged"
        assert by_col["amt"]["status"] == "changed"
        assert by_col["gone"]["status"] == "removed"
        assert by_col["added"]["status"] == "added"
        assert out["cross_source"] is False
        assert out["changed_count"] == 3

    def test_one_missing_ref_returns_error(self):
        good = self._ref("llm:1", [])
        with patch.object(ps, "_columns_for_ref", side_effect=[good, None]):
            out = ps.compare_transformation_versions("c", "s", "t", "llm:1", "llm:99", "JOB", "1")
        assert "error" in out
        assert out["to"] is None


# ---------------------------------------------------------------------------
# _fetch_source dispatch
# ---------------------------------------------------------------------------

class TestFetchSourceDispatch:
    def test_notebook_dispatch(self):
        with patch.object(ps, "_fetch_notebook_source", return_value="NB") as m:
            assert ps._fetch_source("notebook", "/W/nb") == "NB"
        m.assert_called_once()

    def test_query_dispatch(self):
        with patch.object(ps, "_fetch_query_source", return_value="SQL") as m:
            assert ps._fetch_source("QUERY", "q1") == "SQL"
        m.assert_called_once()

    def test_job_dispatch(self):
        with patch.object(ps, "_fetch_job_source", return_value="JOB") as m:
            assert ps._fetch_source("JOB", "1") == "JOB"
        m.assert_called_once()

    def test_pipeline_dispatch(self):
        with patch.object(ps, "_fetch_pipeline_source", return_value="PIPE") as m:
            assert ps._fetch_source("PIPELINE", "pid") == "PIPE"
        m.assert_called_once()

    def test_unknown_type_returns_empty(self):
        assert ps._fetch_source("EXTERNAL", "x") == ""


# ---------------------------------------------------------------------------
# _fetch_target_columns
# ---------------------------------------------------------------------------

class TestFetchTargetColumns:
    def test_returns_column_names(self):
        gov = {"columns": [{"name": "a"}, {"name": "b"}, {"name": None}]}
        with patch("backend.server.governance.get_table_governance", return_value=gov):
            out = ps._fetch_target_columns("cat.sch.tbl")
        assert out == ["a", "b"]

    def test_non_three_part_name_returns_empty(self):
        assert ps._fetch_target_columns("just_table") == []

    def test_governance_error_returns_empty(self):
        with patch("backend.server.governance.get_table_governance", side_effect=RuntimeError("x")):
            assert ps._fetch_target_columns("c.s.t") == []


# ---------------------------------------------------------------------------
# _fetch_pipeline_source — end-to-end (raw REST spec + library shapes)
# ---------------------------------------------------------------------------

class TestFetchPipelineSourceFull:
    def _client(self, libraries):
        client = MagicMock()
        client.api_client.do.return_value = {"spec": {"libraries": libraries}}
        return client

    def test_notebook_file_and_glob_libraries_combined(self):
        client = self._client([
            {"notebook": {"path": "/W/nb"}},
            {"file": {"path": "/W/etl.py"}},
            {"glob": {"include": "/W/proj/transformations/**"}},
        ])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="NB_SRC"), \
             patch.object(ps, "_fetch_workspace_file", return_value="FILE_SRC"), \
             patch.object(ps, "_list_workspace_source_files",
                          return_value=["/W/proj/transformations/a.py"]):
            src = ps._fetch_pipeline_source("pid")
        assert "NB_SRC" in src
        assert "FILE_SRC" in src

    def test_duplicate_path_skipped(self):
        client = self._client([
            {"notebook": {"path": "/W/nb"}},
            {"notebook": {"path": "/W/nb"}},  # dupe
        ])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="NB") as m:
            ps._fetch_pipeline_source("pid")
        assert m.call_count == 1

    def test_rest_spec_error_notes_diag_and_yields_empty(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("403 pipeline forbidden")
        diag = ps._FetchDiag()
        with patch.object(ps, "_get_client", return_value=client):
            src = ps._fetch_pipeline_source("pid", diag=diag)
        assert src == ""
        assert "pipeline:pid" in diag.denied_paths

    def test_outer_exception_returns_empty(self):
        with patch.object(ps, "_get_client", side_effect=RuntimeError("boom")):
            assert ps._fetch_pipeline_source("pid") == ""


# ---------------------------------------------------------------------------
# analyze_producer — unreadable-source reason_code branches
# ---------------------------------------------------------------------------

class TestAnalyzeProducerUnreadableBranches:
    def test_entity_missing_reason_code(self):
        def fake_fetch(entity_type, entity_id, diag=None):
            if diag is not None:
                diag.note_exception("job:1", Exception("job was not found"))
            return ""

        with patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps, "_fetch_source", side_effect=fake_fetch):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["reason_code"] == "entity_missing"
        assert "no longer exists" in out["detail"]

    def test_no_source_reason_code(self):
        with patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps, "_fetch_source", return_value=""):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["reason_code"] == "no_source"
        assert "No source code available" in out["detail"]

    def test_access_denied_without_sp_client_id(self):
        def fake_fetch(entity_type, entity_id, diag=None):
            if diag is not None:
                diag.note_exception("/W/x", RuntimeError("PERMISSION_DENIED"))
            return ""

        with patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps, "_fetch_source", side_effect=fake_fetch), \
             patch.object(ps, "APP_SP_CLIENT_ID", ""):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["reason_code"] == "access_denied"
        assert out["app_service_principal"] is None
        assert "service principal" in out["detail"]


# ---------------------------------------------------------------------------
# compare_producers + _cmp_key
# ---------------------------------------------------------------------------

class TestCompareProducersFull:
    def test_skips_producers_missing_type_or_id(self):
        with patch.object(ps, "analyze_producer") as m:
            out = ps.compare_producers("c", "s", "t", [
                {"entity_type": "", "entity_id": "x"},
                {"entity_type": "JOB", "entity_id": ""},
            ])
        m.assert_not_called()
        assert out["producers"] == []
        assert out["column_count"] == 0

    def test_analyze_producer_exception_marks_unavailable(self):
        with patch.object(ps, "analyze_producer", side_effect=RuntimeError("kaboom")):
            out = ps.compare_producers("c", "s", "t", [
                {"entity_type": "JOB", "entity_id": "a"},
            ])
        assert out["producers"][0]["source"] == "unavailable"

    def test_source_label_variants(self):
        results = [
            {"source": "stored", "version": 2, "llm_model": "m",
             "columns": [{"target_column": "x", "expression": "x", "source_columns": ["x"]}]},
            {"source": "unavailable", "columns": []},
        ]
        with patch.object(ps, "analyze_producer", side_effect=results):
            out = ps.compare_producers("c", "s", "t", [
                {"entity_type": "JOB", "entity_id": "a"},
                {"entity_type": "PIPELINE", "entity_id": "b"},
            ])
        labels = {p["key"]: p["label"] for p in out["producers"]}
        assert labels["JOB:a"].startswith("Stored LLM v2")
        assert labels["PIPELINE:b"] == "Source unavailable"

    def test_cmp_key_stable_for_equivalent_columns(self):
        c1 = {"expression": "a + b", "source_columns": ["b", "a"]}
        c2 = {"transformation": "a + b", "source_columns": ["a", "b"]}
        assert ps._cmp_key(c1) == ps._cmp_key(c2)
