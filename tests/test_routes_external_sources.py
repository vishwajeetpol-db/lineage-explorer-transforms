"""Tests for backend/routes/external_sources.py — OL Bridge (v2.5.3).

Covers external platform registration, OL event ingestion, source listing,
and event inspection.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestOLBridgeRegister:
    """POST /api/external/ol-bridge/register — register an external platform."""

    def test_register_requires_body(self, app_client):
        resp = app_client.post("/api/external/ol-bridge/register")
        assert resp.status_code == 422

    def test_register_valid_platform(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/register", json={
                "platform": "snowflake",
                "display_name": "Snowflake Prod",
                "description": "Snowflake Horizon lineage",
            })
            assert resp.status_code in (200, 201)
            data = resp.json()
            assert "source_id" in data


class TestOLBridgeIngest:
    """POST /api/external/ol-bridge/ingest/{source_id} — receive OL events."""

    def test_ingest_valid_event(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/ingest/test-source-123", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "run-1"},
                "job": {"namespace": "snowflake", "name": "etl_job"},
                "inputs": [{"namespace": "snowflake", "name": "raw.events"}],
                "outputs": [{"namespace": "snowflake", "name": "analytics.events_clean"}],
            })
            assert resp.status_code == 200

    def test_ingest_batch_events(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            events = [
                {"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                 "run": {"runId": f"run-{i}"}, "job": {"namespace": "dbt", "name": f"model_{i}"},
                 "inputs": [], "outputs": []}
                for i in range(5)
            ]
            resp = app_client.post("/api/external/ol-bridge/ingest/test-source-123", json=events)
            assert resp.status_code == 200


class TestOLBridgeSources:
    """GET /api/external/ol-bridge/sources — list registered sources."""

    def test_returns_sources_list(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"source_id": "s1", "platform": "snowflake",
                 "display_name": "Snowflake Prod", "total_events": "42",
                 "last_push_at": "2026-07-20T12:00:00Z", "active": "true"}
            ]
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_empty_sources(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200


class TestOLBridgeEvents:
    """GET /api/external/ol-bridge/events — inspect received events."""

    def test_returns_events_list(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events")
            assert resp.status_code == 200

    def test_filter_by_source_id(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "source_id": "test-source-123"
            })
            assert resp.status_code == 200

    def test_filter_by_platform(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "platform": "snowflake"
            })
            assert resp.status_code == 200
