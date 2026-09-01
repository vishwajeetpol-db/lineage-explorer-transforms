"""Tests for backend.server.producer_source (v2.6.0 changes).

Covers:
- _is_access_error / _FetchDiag classification (access_denied vs entity_missing)
- _fetch_pipeline_source handling of notebook / file / glob library shapes via
  the raw REST pipeline spec (fixes "LLM unavailable" on modern pipelines)
- analyze_producer's structured reason_code on unreadable source
- compare_producers: per-column matrix, divergence flagging, and per-entity
  resolution (NOT table-level precedence)
"""
from unittest.mock import patch, MagicMock

import pytest


class TestAccessErrorClassifier:
    def test_permission_denied_message_is_access_error(self):
        from backend.server.producer_source import _is_access_error
        assert _is_access_error(RuntimeError("PERMISSION_DENIED: user does not have")) is True

    def test_does_not_have_permissions_is_access_error(self):
        from backend.server.producer_source import _is_access_error
        assert _is_access_error(Exception("User X does not have View permissions on job 1")) is True

    def test_403_is_access_error(self):
        from backend.server.producer_source import _is_access_error
        assert _is_access_error(Exception("HTTP 403 Forbidden")) is True

    def test_not_found_is_not_access_error(self):
        from backend.server.producer_source import _is_access_error
        assert _is_access_error(Exception("RESOURCE_DOES_NOT_EXIST: nope")) is False


class TestFetchDiag:
    def test_access_denied_records_path(self):
        from backend.server.producer_source import _FetchDiag
        d = _FetchDiag()
        d.note_exception("/Workspace/x/nb", RuntimeError("PERMISSION_DENIED"))
        assert d.access_denied is True
        assert "/Workspace/x/nb" in d.denied_paths

    def test_not_found_sets_entity_missing(self):
        from backend.server.producer_source import _FetchDiag
        d = _FetchDiag()
        d.note_exception("pipeline:abc", Exception("The specified pipeline abc was not found."))
        assert d.entity_missing is True
        assert d.access_denied is False

    def test_denied_paths_deduped(self):
        from backend.server.producer_source import _FetchDiag
        d = _FetchDiag()
        d.note_exception("/p", RuntimeError("403"))
        d.note_exception("/p", RuntimeError("403"))
        assert d.denied_paths == ["/p"]


class TestFetchPipelineSourceLibraryShapes:
    """_fetch_pipeline_source must handle notebook / file / glob libraries,
    reading the RAW pipeline spec via REST (older SDKs drop the glob field)."""

    def _client_with_spec(self, libraries):
        client = MagicMock()
        client.api_client.do.return_value = {"spec": {"libraries": libraries}}
        return client

    def test_notebook_library(self):
        from backend.server import producer_source as ps
        client = self._client_with_spec([{"notebook": {"path": "/W/nb1"}}])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_notebook_source", return_value="print('nb1')") as mock_nb:
            src = ps._fetch_pipeline_source("pid")
        assert "nb1" in src
        mock_nb.assert_called()

    def test_glob_library_walks_and_exports_files(self):
        from backend.server import producer_source as ps
        client = self._client_with_spec([
            {"glob": {"include": "/Workspace/Users/x/proj/src/transformations/**"}}
        ])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_list_workspace_source_files",
                          return_value=["/Workspace/Users/x/proj/src/transformations/a.py"]) as mock_walk, \
             patch.object(ps, "_fetch_workspace_file", return_value="df = spark.read...") as mock_file:
            src = ps._fetch_pipeline_source("pid")
        # glob base dir is walked (wildcards stripped)
        base = mock_walk.call_args[0][0]
        assert base == "/Workspace/Users/x/proj/src/transformations"
        assert "spark.read" in src
        mock_file.assert_called()

    def test_file_library(self):
        from backend.server import producer_source as ps
        client = self._client_with_spec([{"file": {"path": "/Workspace/x/etl.py"}}])
        with patch.object(ps, "_get_client", return_value=client), \
             patch.object(ps, "_fetch_workspace_file", return_value="SELECT 1") as mock_file:
            src = ps._fetch_pipeline_source("pid")
        assert "SELECT 1" in src
        mock_file.assert_called_with("/Workspace/x/etl.py", diag=None)

    def test_empty_libraries_returns_empty(self):
        from backend.server import producer_source as ps
        client = self._client_with_spec([])
        with patch.object(ps, "_get_client", return_value=client):
            assert ps._fetch_pipeline_source("pid") == ""


class TestAnalyzeProducerReasonCode:
    """analyze_producer returns a structured reason_code when source is unreadable."""

    def test_access_denied_reason_code(self):
        from backend.server import producer_source as ps

        def fake_fetch(entity_type, entity_id, diag=None):
            if diag is not None:
                diag.note_exception("/Workspace/x/nb", RuntimeError("PERMISSION_DENIED"))
            return ""

        with patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps, "_fetch_source", side_effect=fake_fetch), \
             patch.object(ps, "APP_SP_CLIENT_ID", "sp-123"):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["reason_code"] == "access_denied"
        assert "/Workspace/x/nb" in out["denied_paths"]
        assert out["app_service_principal"] == "sp-123"

    def test_no_source_reason_code(self):
        from backend.server import producer_source as ps
        with patch.object(ps.llm_client, "is_llm_configured", return_value=True), \
             patch.object(ps.analysis_store, "get_latest_version", return_value=None), \
             patch.object(ps, "_fetch_source", return_value=""):
            out = ps.analyze_producer("JOB", "1", "c.s.t", force_rerun=True)
        assert out["reason_code"] == "no_source"


class TestCompareProducers:
    """Multi-producer per-column comparison matrix."""

    def _resolved(self, producer_cols):
        """Build an analyze_producer-style return with the given columns."""
        return {"source": "llm", "version": 1, "llm_model": "m", "columns": producer_cols}

    def test_divergent_columns_flagged(self):
        from backend.server import producer_source as ps
        producer_a = self._resolved([
            {"target_column": "order_id", "source_columns": ["order_id"], "expression": "order_id"},
            {"target_column": "amount_usd", "source_columns": ["amount", "fx_rate"],
             "expression": "ROUND(amount * fx_rate, 2)"},
        ])
        producer_b = self._resolved([
            {"target_column": "order_id", "source_columns": ["order_id"], "expression": "order_id"},
            {"target_column": "amount_usd", "source_columns": ["amount"],
             "expression": "ROUND(amount * 1.10, 2)"},
        ])
        with patch.object(ps, "analyze_producer", side_effect=[producer_a, producer_b]):
            out = ps.compare_producers("c", "s", "orders_curated", [
                {"entity_type": "JOB", "entity_id": "a"},
                {"entity_type": "JOB", "entity_id": "b"},
            ])
        assert out["column_count"] == 2
        assert out["divergent_count"] == 1
        by_col = {r["column"]: r for r in out["columns"]}
        assert by_col["order_id"]["divergent"] is False
        assert by_col["amount_usd"]["divergent"] is True
        # matrix has one cell per producer
        assert len(by_col["amount_usd"]["cells"]) == 2

    def test_uses_analyze_producer_not_table_precedence(self):
        """Each producer must resolve from ITS OWN source (analyze_producer),
        not resolve_column_transformations — else a table-keyed captured plan
        returns identical results for every producer and hides divergence."""
        from backend.server import producer_source as ps
        with patch.object(ps, "analyze_producer", return_value=self._resolved([])) as mock_ap, \
             patch.object(ps, "resolve_column_transformations") as mock_resolve:
            ps.compare_producers("c", "s", "t", [
                {"entity_type": "JOB", "entity_id": "a"},
                {"entity_type": "PIPELINE", "entity_id": "b"},
            ])
        assert mock_ap.call_count == 2
        mock_resolve.assert_not_called()

    def test_missing_column_in_one_producer_is_divergent(self):
        from backend.server import producer_source as ps
        a = self._resolved([{"target_column": "x", "source_columns": ["x"], "expression": "x"},
                            {"target_column": "y", "source_columns": ["y"], "expression": "y"}])
        b = self._resolved([{"target_column": "x", "source_columns": ["x"], "expression": "x"}])
        with patch.object(ps, "analyze_producer", side_effect=[a, b]):
            out = ps.compare_producers("c", "s", "t", [
                {"entity_type": "JOB", "entity_id": "a"},
                {"entity_type": "JOB", "entity_id": "b"},
            ])
        by_col = {r["column"]: r for r in out["columns"]}
        # y present in a, absent in b → divergent
        assert by_col["y"]["divergent"] is True
        y_cells = {c["producer"]: c for c in by_col["y"]["cells"]}
        assert y_cells["JOB:a"]["present"] is True
        assert y_cells["JOB:b"]["present"] is False


class TestCompareProducersRoute:
    """POST /api/column-transformations/compare-producers."""

    def test_requires_at_least_two_producers(self, app_client):
        resp = app_client.post("/api/column-transformations/compare-producers", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "producers": [{"entity_type": "JOB", "entity_id": "a"}],
        })
        assert resp.status_code == 400

    def test_injection_in_entity_id_400(self, app_client):
        resp = app_client.post("/api/column-transformations/compare-producers", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "producers": [
                {"entity_type": "JOB", "entity_id": "a'; DROP--"},
                {"entity_type": "JOB", "entity_id": "b"},
            ],
        })
        assert resp.status_code == 400

    def test_valid_returns_matrix(self, app_client):
        # A non-admin caller must clear _assert_producers_of, which asks
        # system.access.table_lineage whether each (entity_type, entity_id) really
        # writes the target. That check fails CLOSED (503) when it cannot run, so
        # its SQL has to be mocked here — previously this test left it unmocked and
        # only passed when a mock WorkspaceClient happened to have leaked in from an
        # earlier test file; run on its own it reached the network and hung.
        recorded = [{"et": "JOB", "eid": "a"}, {"et": "JOB", "eid": "b"}]
        with patch("backend.routes.lineage._execute_sql", return_value=recorded), \
             patch("backend.routes.lineage.compare_producers", return_value={
                 "table_full_name": "c.s.t", "producers": [], "columns": [],
                 "divergent_count": 0, "column_count": 0,
             }):
            resp = app_client.post("/api/column-transformations/compare-producers", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "producers": [
                    {"entity_type": "JOB", "entity_id": "a"},
                    {"entity_type": "JOB", "entity_id": "b"},
                ],
            })
        assert resp.status_code == 200
        assert "columns" in resp.json()

    def test_unrecorded_producer_is_rejected_for_non_admin(self, app_client):
        """The batch guard must still refuse a producer UC never recorded — one
        query now answers all N, so a partial result has to fail."""
        only_a = [{"et": "JOB", "eid": "a"}]
        with patch("backend.routes.lineage._execute_sql", return_value=only_a), \
             patch("backend.routes.lineage.compare_producers") as compare:
            resp = app_client.post("/api/column-transformations/compare-producers", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "producers": [
                    {"entity_type": "JOB", "entity_id": "a"},
                    {"entity_type": "JOB", "entity_id": "b"},
                ],
            })
        assert resp.status_code == 403
        compare.assert_not_called()

    def test_producer_fan_out_is_capped(self, app_client):
        """Each producer costs an LLM resolution, and the list was previously
        bounded only by "at least 2"."""
        with patch("backend.routes.lineage._execute_sql", return_value=[]), \
             patch("backend.routes.lineage.compare_producers") as compare:
            resp = app_client.post("/api/column-transformations/compare-producers", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "producers": [
                    {"entity_type": "JOB", "entity_id": f"j{i}"} for i in range(25)
                ],
            })
        assert resp.status_code == 400
        assert "at most" in resp.json()["detail"]
        compare.assert_not_called()
