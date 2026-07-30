"""Tests for the analyze-producer / column-transformations routes
(backend/routes/lineage.py — analyze_router + lineage_ext_router).

Covers the LLM producer-analysis and unified column-transformation endpoints:
validation, entity-id injection guards, and happy paths with the service layer
mocked. (compare-producers has its own coverage in test_producer_source.py.)
"""
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
