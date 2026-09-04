"""Tests for the analyze-producer / column-transformations routes
(backend/routes/lineage.py — analyze_router + lineage_ext_router).

Covers the LLM producer-analysis and unified column-transformation endpoints:
validation, entity-id injection guards, and happy paths with the service layer
mocked. (compare-producers has its own coverage in test_producer_source.py.)
"""
import json
from unittest.mock import patch

import pytest


class TestAnalyzeProducer:
    def test_post_requires_entity_id(self, app_client):
        resp = app_client.post("/api/analyze-producer", json={
            "entity_type": "JOB", "entity_id": "", "target_table": "c.s.t"})
        assert resp.status_code == 400

    def test_post_rejects_bad_target_table(self, app_client):
        resp = app_client.post("/api/analyze-producer", json={
            "entity_type": "JOB", "entity_id": "123", "target_table": "not_fqn"})
        assert resp.status_code == 400

    def test_post_injection_in_entity_id(self, app_client):
        resp = app_client.post("/api/analyze-producer", json={
            "entity_type": "JOB", "entity_id": "1; DROP TABLE--", "target_table": "c.s.t"})
        assert resp.status_code == 400

    def test_post_ok(self, app_client):
        # _assert_producer_of is patched out here: this test covers the handler, and
        # the guard itself has dedicated coverage in TestProducerAuthorization.
        with patch("backend.routes.lineage._assert_producer_of"), \
             patch("backend.routes.lineage.analyze_producer",
                   return_value={"source": "llm", "columns": [], "version": 1}):
            resp = app_client.post("/api/analyze-producer", json={
                "entity_type": "JOB", "entity_id": "123", "target_table": "c.s.t"})
        assert resp.status_code == 200
        assert resp.json()["source"] == "llm"

    def test_models_endpoint_ok(self, app_client):
        resp = app_client.get("/api/analyze-producer/models")
        # Falls back to a curated list even if serving inventory can't be read.
        assert resp.status_code == 200
        body = resp.json()
        assert "models" in body and "default" in body

    def test_history_requires_admin(self, non_admin_client):
        resp = non_admin_client.get("/api/analyze-producer/history")
        assert resp.status_code == 403

    def test_history_admin_ok(self, admin_client):
        with patch("backend.routes.lineage.list_analyses", return_value=[]):
            resp = admin_client.get("/api/analyze-producer/history")
        assert resp.status_code == 200


class TestColumnTransformations:
    def test_requires_valid_catalog(self, app_client):
        resp = app_client.post("/api/column-transformations", json={
            "catalog": "bad;", "schema_name": "s", "table": "t"})
        assert resp.status_code == 400

    def test_injection_in_entity_id(self, app_client):
        resp = app_client.post("/api/column-transformations", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "entity_type": "JOB", "entity_id": "1'; DROP--"})
        assert resp.status_code == 400

    def test_resolve_ok(self, app_client):
        with patch("backend.routes.lineage.resolve_column_transformations",
                   return_value={"source": "plan_capture", "columns": [], "source_label": "x"}):
            resp = app_client.post("/api/column-transformations", json={
                "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code == 200
        assert resp.json()["source"] == "plan_capture"

    def test_versions_ok(self, app_client):
        with patch("backend.routes.lineage.list_all_versions", return_value=[]):
            resp = app_client.post("/api/column-transformations/versions", json={
                "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code == 200
        assert "versions" in resp.json()

    # ---- overview ----
    def test_overview_rejects_bad_catalog(self, app_client):
        resp = app_client.post("/api/column-transformations/overview", json={
            "catalog": "bad;", "schema_name": "s", "table": "t"})
        assert resp.status_code == 400

    def test_overview_rejects_bad_entity_id(self, app_client):
        resp = app_client.post("/api/column-transformations/overview", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "entity_type": "JOB", "entity_id": "1'; DROP--"})
        assert resp.status_code == 400

    def test_overview_ok(self, app_client):
        with patch("backend.routes.lineage.overview_column_transformations",
                   return_value={"summary": "does joins", "columns": [], "source": "llm"}):
            resp = app_client.post("/api/column-transformations/overview", json={
                "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code == 200
        assert resp.json()["summary"] == "does joins"

    def test_overview_service_error_500(self, app_client):
        with patch("backend.routes.lineage.overview_column_transformations",
                   side_effect=RuntimeError("cache boom")):
            resp = app_client.post("/api/column-transformations/overview", json={
                "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code == 500

    # ---- deep-analyze (streaming NDJSON) ----
    def test_deep_analyze_requires_entity(self, app_client):
        resp = app_client.post("/api/column-transformations/deep-analyze", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "entity_type": "", "entity_id": ""})
        assert resp.status_code == 400

    def test_deep_analyze_streams_ndjson(self, app_client):
        events = [
            {"type": "step", "step": "start", "status": "running", "message": "go"},
            {"type": "result", "derived": True, "columns": [{"target_column": "x"}], "version": 3},
        ]
        with patch("backend.routes.lineage._assert_producer_of"), \
             patch("backend.server.framework_analysis.deep_analyze_stream",
                   return_value=iter(events)):
            resp = app_client.post("/api/column-transformations/deep-analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "entity_type": "JOB", "entity_id": "123"})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        lines = [l for l in resp.text.splitlines() if l.strip()]
        assert len(lines) == 2
        assert json.loads(lines[-1])["derived"] is True

    def test_deep_analyze_stream_error_is_emitted(self, app_client):
        def boom(*a, **k):
            raise RuntimeError("mid-flight")
        with patch("backend.routes.lineage._assert_producer_of"), \
             patch("backend.server.framework_analysis.deep_analyze_stream", side_effect=boom):
            resp = app_client.post("/api/column-transformations/deep-analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "entity_type": "JOB", "entity_id": "123"})
        assert resp.status_code == 200
        last = json.loads([l for l in resp.text.splitlines() if l.strip()][-1])
        assert last["type"] == "error" and "mid-flight" in last["message"]

    def test_compare_rejects_bad_ref(self, app_client):
        resp = app_client.post("/api/column-transformations/compare", json={
            "catalog": "c", "schema_name": "s", "table": "t",
            "ref_from": "not-a-ref", "ref_to": "llm:2"})
        assert resp.status_code == 400

    def test_compare_ok(self, app_client):
        with patch("backend.routes.lineage.compare_transformation_versions",
                   return_value={"column_diffs": [], "changed_count": 0}):
            resp = app_client.post("/api/column-transformations/compare", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "ref_from": "plan_capture:1", "ref_to": "llm:2"})
        assert resp.status_code == 200


class TestProducerAuthorization:
    """The source fetch runs as the app SP, whose read scope is broader than any
    one caller's, and `entity_id` accepts a free-form workspace path. Without a
    producer check a non-admin could aim it at an unrelated notebook and get the
    source back — so the pair must be one UC actually recorded for the target."""

    _NB = {"entity_type": "NOTEBOOK", "entity_id": "/Users/someone-else/private",
           "target_table": "c.s.t"}

    def test_unrecorded_producer_is_refused(self, app_client):
        # No lineage row linking this notebook to the target → 403, and the
        # source fetch must never be reached.
        with patch("backend.routes.lineage._execute_sql", return_value=[]), \
             patch("backend.routes.lineage.analyze_producer") as mock_analyze:
            resp = app_client.post("/api/analyze-producer", json=self._NB)
        assert resp.status_code == 403
        assert "not a recorded producer" in resp.json()["detail"]
        mock_analyze.assert_not_called()

    def test_recorded_producer_is_allowed(self, app_client):
        with patch("backend.routes.lineage._execute_sql", return_value=[{"ok": 1}]), \
             patch("backend.routes.lineage.analyze_producer",
                   return_value={"source": "llm", "columns": []}) as mock_analyze:
            resp = app_client.post("/api/analyze-producer", json=self._NB)
        assert resp.status_code == 200
        mock_analyze.assert_called_once()

    def test_admin_bypasses_the_producer_check(self, admin_client):
        # Admins already have the broader access the guard is protecting.
        with patch("backend.routes.lineage._execute_sql") as mock_sql, \
             patch("backend.routes.lineage.analyze_producer",
                   return_value={"source": "llm", "columns": []}):
            resp = admin_client.post("/api/analyze-producer", json=self._NB)
        assert resp.status_code == 200
        mock_sql.assert_not_called()

    def test_lookup_failure_fails_closed(self, app_client):
        # If the lineage check can't run we refuse rather than trusting the caller.
        with patch("backend.routes.lineage._execute_sql", side_effect=RuntimeError("no perms")), \
             patch("backend.routes.lineage.analyze_producer") as mock_analyze:
            resp = app_client.post("/api/analyze-producer", json=self._NB)
        assert resp.status_code == 503
        mock_analyze.assert_not_called()

    def test_deep_analyze_is_guarded_too(self, app_client):
        with patch("backend.routes.lineage._execute_sql", return_value=[]), \
             patch("backend.server.framework_analysis.deep_analyze_stream") as mock_stream:
            resp = app_client.post("/api/column-transformations/deep-analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t",
                "entity_type": "NOTEBOOK", "entity_id": "/Users/someone-else/private"})
        assert resp.status_code == 403
        mock_stream.assert_not_called()


class TestStoredSourceRedaction:
    """`analysis_store._decode_row` returns `source_code` verbatim, so any endpoint
    returning a full row hands out the producer's code. Only admins need it."""

    _ROW = {"version": 1, "columns": [], "source_hash": "abc", "source_code": "SECRET TOKEN=dapi123"}

    def test_version_redacts_source_for_non_admin(self, app_client):
        with patch("backend.routes.lineage._assert_producer_of"), \
             patch("backend.routes.lineage.get_version", return_value=dict(self._ROW)):
            resp = app_client.get("/api/analyze-producer/version", params={
                "entity_type": "JOB", "entity_id": "1", "target_table": "c.s.t", "version": 1})
        assert resp.status_code == 200
        body = resp.json()
        assert body["source_code"] is None
        assert body["source_code_redacted"] is True

    def test_version_keeps_source_for_admin(self, admin_client):
        with patch("backend.routes.lineage.get_version", return_value=dict(self._ROW)):
            resp = admin_client.get("/api/analyze-producer/version", params={
                "entity_type": "JOB", "entity_id": "1", "target_table": "c.s.t", "version": 1})
        assert resp.status_code == 200
        assert resp.json()["source_code"] == "SECRET TOKEN=dapi123"

    def test_compare_redacts_both_sides_for_non_admin(self, app_client):
        with patch("backend.routes.lineage._assert_producer_of"), \
             patch("backend.routes.lineage.get_version", side_effect=[dict(self._ROW), dict(self._ROW)]):
            resp = app_client.get("/api/analyze-producer/compare", params={
                "entity_type": "JOB", "entity_id": "1", "target_table": "c.s.t",
                "from_version": 1, "to_version": 2})
        assert resp.status_code == 200
        body = resp.json()
        assert body["from"]["source_code"] is None and body["to"]["source_code"] is None
        # the derived signal survives redaction
        assert body["source_changed"] is False


class TestExplainLineage:
    def test_requires_llm_configured(self, app_client):
        with patch("backend.server.llm.is_llm_configured", return_value=False):
            resp = app_client.post("/api/lineage/explain", json={
                "focus_table": "c.s.t", "nodes": [], "edges": []})
        assert resp.status_code == 503

    def test_requires_focus_table(self, app_client):
        with patch("backend.server.llm.is_llm_configured", return_value=True):
            resp = app_client.post("/api/lineage/explain", json={
                "focus_table": "  ", "nodes": [], "edges": []})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.server.llm.is_llm_configured", return_value=True), \
             patch("backend.server.llm.explain_lineage_graph",
                   return_value={"summary": "flows A to B", "steps": [{"title": "t", "detail": "d"}]}) as mock_ex:
            resp = app_client.post("/api/lineage/explain", json={
                "focus_table": "c.s.t",
                "nodes": [{"id": "n1", "label": "A", "type": "Dataset"}],
                "edges": [{"source": "n1", "target": "n2"}],
                "detail": "data"})
        assert resp.status_code == 200
        assert resp.json()["summary"] == "flows A to B"
        # detail normalized and passed through
        assert mock_ex.call_args.args[3] == "data"

    def test_bad_detail_defaults_to_data_and_processing(self, app_client):
        with patch("backend.server.llm.is_llm_configured", return_value=True), \
             patch("backend.server.llm.explain_lineage_graph",
                   return_value={"summary": "s", "steps": []}) as mock_ex:
            resp = app_client.post("/api/lineage/explain", json={
                "focus_table": "c.s.t", "nodes": [], "edges": [], "detail": "weird"})
        assert resp.status_code == 200
        assert mock_ex.call_args.args[3] == "data_and_processing"

    def test_service_error_500(self, app_client):
        with patch("backend.server.llm.is_llm_configured", return_value=True), \
             patch("backend.server.llm.explain_lineage_graph", side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/lineage/explain", json={
                "focus_table": "c.s.t", "nodes": [], "edges": []})
        assert resp.status_code == 500


class TestLineageExtensions:
    def test_column_path_requires_params(self, app_client):
        resp = app_client.get("/api/lineage/column-path", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_column_path_injection_400(self, app_client):
        resp = app_client.get("/api/lineage/column-path", params={
            "catalog": "bad;", "schema": "s", "table": "t", "column": "col"})
        assert resp.status_code == 400

    def test_freshness_ok(self, app_client):
        with patch("backend.routes.lineage._execute_sql",
                   return_value=[{"edge_count": 3, "last_event_at": "2026-07-01T00:00:00Z"}]):
            resp = app_client.get("/api/lineage/freshness", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200

    def test_freshness_injection_400(self, app_client):
        resp = app_client.get("/api/lineage/freshness", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400

    def test_entities_requires_params(self, app_client):
        resp = app_client.get("/api/lineage/entities")
        assert resp.status_code == 422
