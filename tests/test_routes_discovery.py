"""Tests for backend/routes/discovery.py — Search & discovery (v2.5.0).

Fixes:
- C8:  Warehouse stopped / SQL timeout scenarios
- A1:  SQL injection in search query param
- A10: Silent failures when system tables inaccessible

The routes delegate to backend.server.discovery.{search_assets,
find_sensitive_tables, find_orphan_tables} and return wrapped dicts
({"results"/"tables": [...], "count": n}). Service errors are wrapped as
HTTP 500 rather than swallowed into an empty 200.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestSearchEndpoint:
    """GET /api/search — full-text UC asset search."""

    def test_missing_query_returns_422(self, app_client):
        resp = app_client.get("/api/search")
        assert resp.status_code in (422, 400)

    def test_valid_search_returns_results(self, app_client):
        with patch("backend.routes.discovery.search_assets") as mock_search:
            mock_search.return_value = [
                {"catalog_name": "main", "schema_name": "default",
                 "table_name": "orders", "table_type": "MANAGED"}
            ]
            resp = app_client.get("/api/search", params={"q": "orders"})
            assert resp.status_code == 200
            data = resp.json()
            # Results are wrapped under "results" with a "count".
            assert isinstance(data, dict)
            assert isinstance(data["results"], list)
            assert data["count"] == 1

    def test_empty_search_results(self, app_client):
        with patch("backend.routes.discovery.search_assets") as mock_search:
            mock_search.return_value = []
            resp = app_client.get("/api/search", params={"q": "nonexistent_xyz"})
            assert resp.status_code == 200
            assert resp.json() == {"results": [], "count": 0}

    def test_sql_injection_in_search_query(self, app_client):
        """A1: Search query `q` is passed to the service (parameterization is
        the real defense); the route accepts it and returns 200 with results."""
        with patch("backend.routes.discovery.search_assets") as mock_search:
            mock_search.return_value = []
            resp = app_client.get("/api/search", params={
                "q": "orders' UNION SELECT * FROM information_schema.tables--"
            })
            assert resp.status_code in (200, 400)

    def test_warehouse_timeout_returns_error(self, app_client):
        """C8: Warehouse timeout surfaces as 500, not an empty 200 or hang."""
        with patch("backend.routes.discovery.search_assets") as mock_search:
            mock_search.side_effect = RuntimeError("SQL failed: WAREHOUSE_TIMEOUT")
            resp = app_client.get("/api/search", params={"q": "orders"})
            assert resp.status_code in (500, 503)


class TestSensitiveColumns:
    """GET /api/discover/sensitive — PII/sensitive column finder."""

    def test_returns_sensitive_columns(self, app_client):
        with patch("backend.routes.discovery.find_sensitive_tables") as mock_fn:
            mock_fn.return_value = [
                {"catalog_name": "main", "schema_name": "pii",
                 "table_name": "users", "column_name": "email",
                 "tag_name": "pii", "tag_value": "true"}
            ]
            resp = app_client.get("/api/discover/sensitive", params={"catalog": "main"})
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)
            assert isinstance(data["tables"], list)

    def test_sql_injection_in_catalog_param(self, app_client):
        """A1: An injection-laden catalog fails the identifier regex -> 400."""
        with patch("backend.routes.discovery.find_sensitive_tables") as mock_fn:
            mock_fn.return_value = []
            resp = app_client.get("/api/discover/sensitive", params={
                "catalog": "main'; DROP TABLE --"
            })
            # Contains quote/space/semicolon -> rejected by validation.
            assert resp.status_code == 400

    def test_system_tables_unavailable(self, app_client):
        """C1/A10: System tables disabled — surfaced as 500, not empty 200."""
        with patch("backend.routes.discovery.find_sensitive_tables") as mock_fn:
            mock_fn.side_effect = RuntimeError(
                "INSUFFICIENT_PERMISSIONS: Cannot access system.information_schema"
            )
            resp = app_client.get("/api/discover/sensitive", params={"catalog": "main"})
            assert resp.status_code in (500, 503)


class TestOrphanTables:
    """GET /api/discover/orphans — orphan-table detector."""

    def test_returns_orphans_list(self, app_client):
        with patch("backend.routes.discovery.find_orphan_tables") as mock_fn:
            mock_fn.return_value = [
                {"catalog_name": "main", "schema_name": "default",
                 "table_name": "stale_table", "last_accessed": None}
            ]
            resp = app_client.get("/api/discover/orphans", params={"catalog": "main"})
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, dict)
            assert isinstance(data["tables"], list)

    def test_no_orphans_found(self, app_client):
        with patch("backend.routes.discovery.find_orphan_tables") as mock_fn:
            mock_fn.return_value = []
            resp = app_client.get("/api/discover/orphans", params={"catalog": "main"})
            assert resp.status_code == 200
            assert resp.json() == {"tables": [], "count": 0}

    def test_warehouse_stopped(self, app_client):
        """C8: Warehouse stopped — surfaced as an explicit 500."""
        with patch("backend.routes.discovery.find_orphan_tables") as mock_fn:
            mock_fn.side_effect = RuntimeError("No SQL warehouse available.")
            resp = app_client.get("/api/discover/orphans", params={"catalog": "main"})
            assert resp.status_code in (500, 503)
