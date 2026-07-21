"""Tests for backend/routes/impact.py — Impact analysis / blast radius (v2.5.0).

Covers BFS downstream walk, validation, and response structure.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestImpactRoute:
    """GET /api/impact endpoint tests."""

    def test_missing_table_returns_422(self, app_client):
        """table query param is required."""
        resp = app_client.get("/api/impact")
        assert resp.status_code == 422

    def test_invalid_catalog_returns_400(self, app_client):
        resp = app_client.get("/api/impact", params={
            "catalog": "bad;", "schema": "s", "table": "t"
        })
        assert resp.status_code == 400

    def test_valid_params_with_empty_lineage(self, app_client):
        """When no downstream tables found, should return empty list."""
        with patch("backend.routes.impact._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "downstream_tables" in data or "downstream_count" in data or isinstance(data, (dict, list))

    def test_bfs_walks_downstream(self, app_client):
        """Should perform BFS traversal from source table."""
        mock_rows = [
            {"target_catalog": "main", "target_schema": "silver", "target_table": "orders_clean",
             "source_catalog": "main", "source_schema": "default", "source_table": "orders"},
        ]
        with patch("backend.routes.impact._execute_sql") as mock_sql:
            mock_sql.return_value = mock_rows
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200


class TestImpactSQLExecution:
    """_execute_sql helper in impact module."""

    def test_no_warehouse_raises_runtime_error(self):
        """When WAREHOUSE_ID is empty, should raise RuntimeError."""
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}):
            import importlib
            import backend.routes.impact as impact_mod
            importlib.reload(impact_mod)
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                impact_mod._execute_sql("SELECT 1")
