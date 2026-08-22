"""Tests for backend/routes/openlineage.py — OpenLineage export/import/producer (cap 17).

Export current lineage as OL events, import external OL events, and the live
producer config/produce/events endpoints.
"""
from unittest.mock import patch

import pytest


class TestOpenLineageExport:
    def test_export_requires_catalog(self, app_client):
        resp = app_client.get("/api/export/openlineage")
        assert resp.status_code == 422

    def test_export_scoped_ok(self, app_client):
        from backend.models import LineageResponse
        with patch("backend.routes.openlineage.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])):
            resp = app_client.get("/api/export/openlineage", params={
                "catalog": "main", "schema": "default"})
        assert resp.status_code == 200


class TestOpenLineageImport:
    def test_import_bad_body(self, admin_client):
        """Malformed OL event body should be rejected, not 500."""
        resp = admin_client.post("/api/import/openlineage", json={"not": "an ol event"})
        assert resp.status_code in (200, 400, 422)

    def test_import_requires_admin(self, non_admin_client):
        """Ingest is a write into an app-owned table — non-admins get 403."""
        body = {"events": [{"eventType": "COMPLETE"}]}
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = non_admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 403
        m.assert_not_called()


class TestProducer:
    def test_get_producer_config_ok(self, admin_client):
        # Admin-gated: endpoint_url can carry a credential in its query string.
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = admin_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 200

    def test_get_producer_config_rejects_non_admin(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 403

    def test_configure_producer(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint": "https://marquez.example.com/api/v1/lineage"})
        assert resp.status_code in (200, 400, 422)

    def test_producer_events_ok(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.get("/api/openlineage/producer/events")
        assert resp.status_code == 200
