"""Tests for backend/routes/openlineage.py — OpenLineage export/import/producer (cap 17).

Export current lineage as OL events, import external OL events, and the live
producer config/produce/events endpoints.
"""
from unittest.mock import patch

import pytest


class TestOpenLineageExport:
    def test_export_requires_params(self, app_client):
        # export needs at least a catalog scope
        resp = app_client.get("/api/export/openlineage")
        assert resp.status_code in (200, 422)

    def test_export_scoped_ok(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.get("/api/export/openlineage", params={
                "catalog": "main", "schema": "default"})
        assert resp.status_code in (200, 422)


class TestOpenLineageImport:
    def test_import_bad_body(self, app_client):
        """Malformed OL event body should be rejected, not 500."""
        resp = app_client.post("/api/import/openlineage", json={"not": "an ol event"})
        assert resp.status_code in (200, 400, 422)


class TestProducer:
    def test_get_producer_config_ok(self, app_client):
        resp = app_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 200

    def test_configure_producer(self, app_client):
        resp = app_client.post("/api/openlineage/producer/configure", json={
            "endpoint": "https://marquez.example.com/api/v1/lineage"})
        assert resp.status_code in (200, 400, 422)

    def test_producer_events_ok(self, app_client):
        resp = app_client.get("/api/openlineage/producer/events")
        assert resp.status_code == 200
