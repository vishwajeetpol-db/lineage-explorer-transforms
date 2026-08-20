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
        with patch("backend.routes.lineage.analyze_producer",
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
        with patch("backend.server.framework_analysis.deep_analyze_stream",
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
        with patch("backend.server.framework_analysis.deep_analyze_stream", side_effect=boom):
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
