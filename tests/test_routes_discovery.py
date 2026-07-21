"""Tests for backend/routes/discovery.py — Search & discovery (v2.5.0).

Covers UC asset search, PII/sensitive-column finder, and orphan-table detector.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestSearchEndpoint:
    """GET /api/search — full-text UC asset search."""

    def test_missing_query_returns_422(self, app_client):
        resp = app_client.get("/api/search")
        assert resp.status_code in (422, 400)

    def test_valid_search_returns_results(self, app_client):
        with patch("backend.routes.discovery._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"catalog_name": "main", "schema_name": "default",
                 "table_name": "orders", "table_type": "MANAGED"}
            ]
            resp = app_client.get("/api/search", params={"q": "orders"})
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_empty_search_results(self, app_client):
        with patch("backend.routes.discovery._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/search", params={"q": "nonexistent_xyz"})
            assert resp.status_code == 200
            assert resp.json() == []


class TestSensitiveColumns:
    """GET /api/discover/sensitive — PII/sensitive column finder."""

    def test_returns_sensitive_columns(self, app_client):
        with patch("backend.routes.discovery._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"catalog_name": "main", "schema_name": "pii",
                 "table_name": "users", "column_name": "email",
                 "tag_name": "pii", "tag_value": "true"}
            ]
            resp = app_client.get("/api/discover/sensitive", params={"catalog": "main"})
            assert resp.status_code == 200


class TestOrphanTables:
    """GET /api/discover/orphans — orphan-table detector."""

    def test_returns_orphans_list(self, app_client):
        with patch("backend.routes.discovery._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"catalog_name": "main", "schema_name": "default",
                 "table_name": "stale_table", "last_accessed": None}
            ]
            resp = app_client.get("/api/discover/orphans", params={"catalog": "main"})
            assert resp.status_code == 200

    def test_no_orphans_found(self, app_client):
        with patch("backend.routes.discovery._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/discover/orphans", params={"catalog": "main"})
            assert resp.status_code == 200
