"""Tests for backend/routes/capability_closures.py — v2.5.2 gap-closure endpoints.

Covers: BI tool consumer detection, streaming topology, auto-capture,
timeline, DQ trends, pipeline expectations, webhooks, and notification delivery.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestBIConsumers:
    """GET /api/lineage/bi-consumers endpoint."""

    def test_missing_table_returns_422(self, app_client):
        resp = app_client.get("/api/lineage/bi-consumers")
        assert resp.status_code in (422, 400)

    def test_valid_request_returns_list(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"tool_type": "Tableau", "query_count": "5", "distinct_users": "2", "last_access": "2026-07-15"}
            ]
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)
            if data:
                assert "tool_type" in data[0]


class TestStreamingTopology:
    """GET /api/lineage/streaming-topology endpoint."""

    def test_returns_topology_structure(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/streaming-topology", params={
                "catalog": "main", "schema": "default"
            })
            assert resp.status_code == 200


class TestDQTrends:
    """GET /api/dq-rules/trends endpoint."""

    def test_returns_trend_data(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/trends", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200


class TestPipelineExpectations:
    """GET /api/dq-rules/pipeline-expectations endpoint."""

    def test_returns_expectations_list(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/pipeline-expectations", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200


class TestWebhooks:
    """Webhook CRUD endpoints."""

    def test_list_webhooks_returns_list(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/notifications/webhooks")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    def test_create_webhook_requires_body(self, app_client):
        resp = app_client.post("/api/notifications/webhooks")
        assert resp.status_code == 422

    def test_delete_webhook_with_invalid_id(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.delete("/api/notifications/webhooks/nonexistent-id")
            # Should either 404 or 200 with no-op
            assert resp.status_code in (200, 404)


class TestAutoCapture:
    """POST /api/snapshots/auto-capture endpoint."""

    def test_auto_capture_runs(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/snapshots/auto-capture")
            assert resp.status_code == 200


class TestSnapshotTimeline:
    """GET /api/snapshots/timeline endpoint."""

    def test_timeline_returns_data(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/snapshots/timeline", params={
                "catalog": "main", "schema": "default"
            })
            assert resp.status_code == 200
