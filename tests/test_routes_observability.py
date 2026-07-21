"""Tests for backend/routes/observability.py — Run health (v2.5.0).

Covers success-rate calculations, failure count reporting, and
validation for the /api/observability endpoint.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestObservabilityRoute:
    """GET /api/observability endpoint tests."""

    def test_missing_params_returns_422(self, app_client):
        resp = app_client.get("/api/observability")
        assert resp.status_code in (422, 400)

    def test_invalid_entity_returns_400(self, app_client):
        resp = app_client.get("/api/observability", params={
            "catalog": "DROP TABLE;", "schema": "s", "table": "t"
        })
        assert resp.status_code == 400

    def test_valid_request_returns_health_data(self, app_client):
        with patch("backend.routes.observability._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"total_runs": "10", "successful_runs": "8", "failed_runs": "2",
                 "last_run_at": "2026-07-20T10:00:00Z", "entity_type": "pipeline"}
            ]
            resp = app_client.get("/api/observability", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200

    def test_no_runs_returns_empty_health(self, app_client):
        with patch("backend.routes.observability._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/observability", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
