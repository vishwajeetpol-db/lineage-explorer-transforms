"""Tests for the core lineage routes in backend/main.py.

These are the app's critical path — catalog/schema/table listing, the
end-to-end lineage graph + trace, column lineage, Delta Sharing overlay, and
Excel export. All service calls are mocked (patched at `backend.main.*`); no
live workspace is needed.
"""
from unittest.mock import patch

import pytest


class TestListingRoutes:
    def test_tables(self, app_client):
        with patch("backend.main.list_all_tables", return_value=[{"full_name": "c.s.t"}]):
            resp = app_client.get("/api/tables")
        assert resp.status_code == 200
        assert "tables" in resp.json()

    def test_catalogs(self, app_client):
        with patch("backend.main.list_catalogs", return_value=["main", "analytics"]):
            resp = app_client.get("/api/catalogs")
        assert resp.status_code == 200
        assert "catalogs" in resp.json()

    def test_schemas_requires_catalog(self, app_client):
        resp = app_client.get("/api/schemas")
        assert resp.status_code == 422

    def test_schemas_ok(self, app_client):
        with patch("backend.main.list_schemas", return_value=["default"]):
            resp = app_client.get("/api/schemas", params={"catalog": "main"})
        assert resp.status_code == 200
        assert resp.json()["schemas"] == ["default"]

    def test_schemas_injection_400(self, app_client):
        resp = app_client.get("/api/schemas", params={"catalog": "main'; DROP--"})
        assert resp.status_code == 400


class TestLineageGraph:
    def test_lineage_requires_catalog(self, app_client):
        resp = app_client.get("/api/lineage")
        assert resp.status_code == 422

    def test_lineage_ok(self, app_client):
        with patch("backend.main.get_table_lineage", return_value={"nodes": [], "edges": []}):
            resp = app_client.get("/api/lineage", params={"catalog": "main", "schema": "default"})
        assert resp.status_code == 200
        assert "nodes" in resp.json()

    def test_lineage_injection_400(self, app_client):
        resp = app_client.get("/api/lineage", params={"catalog": "bad;", "schema": "s"})
        assert resp.status_code == 400

    def test_lineage_service_error_500(self, app_client):
        with patch("backend.main.get_table_lineage", side_effect=RuntimeError("SQL failed")):
            resp = app_client.get("/api/lineage", params={"catalog": "main", "schema": "default"})
        assert resp.status_code == 500


class TestLineageTrace:
    def test_trace_requires_table(self, app_client):
        resp = app_client.get("/api/lineage/trace")
        assert resp.status_code == 422

    def test_trace_rejects_non_fqn(self, app_client):
        """table must be catalog.schema.table."""
        resp = app_client.get("/api/lineage/trace", params={"table": "just_a_name"})
        assert resp.status_code == 400

    def test_trace_ok(self, app_client):
        with patch("backend.main.get_lineage_trace", return_value={"nodes": [], "edges": []}):
            resp = app_client.get("/api/lineage/trace", params={"table": "main.default.orders"})
        assert resp.status_code == 200
        assert "edges" in resp.json()


class TestColumns:
    def test_columns_requires_params(self, app_client):
        resp = app_client.get("/api/columns", params={"catalog": "main"})
        assert resp.status_code == 422

    def test_columns_ok(self, app_client):
        with patch("backend.main.get_columns", return_value=[{"name": "id"}]):
            resp = app_client.get("/api/columns", params={
                "catalog": "main", "schema": "default", "table": "orders"})
        assert resp.status_code == 200

    def test_columns_injection_400(self, app_client):
        resp = app_client.get("/api/columns", params={
            "catalog": "main", "schema": "default", "table": "o'; DROP--"})
        assert resp.status_code == 400


class TestColumnLineage:
    def test_column_lineage_ok(self, app_client):
        with patch("backend.main.get_column_lineage", return_value={"upstream": [], "downstream": []}):
            resp = app_client.get("/api/column-lineage", params={
                "catalog": "main", "schema": "default", "table": "orders", "column": "id"})
        assert resp.status_code == 200

    def test_column_lineage_injection_400(self, app_client):
        resp = app_client.get("/api/column-lineage", params={
            "catalog": "main", "schema": "default", "table": "orders", "column": "id'--"})
        assert resp.status_code == 400

    def test_schema_column_lineage_ok(self, app_client):
        with patch("backend.main.get_schema_column_lineage", return_value=[]):
            resp = app_client.get("/api/schema-column-lineage", params={
                "catalog": "main", "schema": "default"})
        assert resp.status_code == 200


class TestSharingAndEntityName:
    def test_sharing_overlay_ok(self, app_client):
        with patch("backend.main.get_sharing_overlay", return_value={"nodes": [], "edges": []}):
            resp = app_client.get("/api/sharing/overlay", params={
                "catalog": "main", "schema": "default"})
        assert resp.status_code == 200

    def test_sharing_overview_ok(self, app_client):
        with patch("backend.main.get_sharing_overview", return_value={"shares": []}):
            resp = app_client.get("/api/sharing/overview")
        assert resp.status_code == 200

    def test_entity_name_requires_params(self, app_client):
        resp = app_client.get("/api/entity-name", params={"entity_type": "JOB"})
        assert resp.status_code == 422

    def test_entity_name_ok(self, app_client):
        with patch("backend.main.resolve_entity_name", return_value={"name": "My Job"}):
            resp = app_client.get("/api/entity-name", params={
                "entity_type": "JOB", "entity_id": "123"})
        assert resp.status_code == 200


class TestExport:
    def test_export_requires_params(self, app_client):
        resp = app_client.get("/api/lineage/export")
        assert resp.status_code == 422
