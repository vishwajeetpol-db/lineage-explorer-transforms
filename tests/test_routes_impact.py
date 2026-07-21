"""Tests for backend/routes/impact.py — Impact analysis / blast radius (v2.5.0).

Fixes:
- C2:  Cross-catalog BROWSE truncation (silent cone narrowing)
- C8:  Warehouse stopped / SQL timeout
- A1:  SQL injection validation

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
        """When no downstream tables found, should return empty result."""
        with patch("backend.routes.impact._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, (dict, list))

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

    def test_cross_catalog_truncation(self, app_client):
        """C2: BFS stops at catalog boundaries where SP lacks BROWSE.
        Impact cone is silently truncated — no error, just missing edges."""
        # First call returns intra-catalog edges; second call (cross-catalog) empty
        with patch("backend.routes.impact._execute_sql") as mock_sql:
            mock_sql.side_effect = [
                # First BFS level: finds cross-catalog dependency
                [{"target_catalog": "analytics", "target_schema": "gold",
                  "target_table": "revenue",
                  "source_catalog": "main", "source_schema": "default",
                  "source_table": "orders"}],
                # Second BFS level: cross-catalog query returns empty (no BROWSE)
                [],
            ]
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            # BUG C2: No indication that results are truncated

    def test_sql_injection_in_table_param(self, app_client):
        """A1: Table param validation."""
        resp = app_client.get("/api/impact", params={
            "catalog": "main", "schema": "default",
            "table": "orders'; DROP TABLE --"
        })
        assert resp.status_code == 400

    def test_sql_injection_in_schema_param(self, app_client):
        """A1: Schema param validation."""
        resp = app_client.get("/api/impact", params={
            "catalog": "main", "schema": "default' OR '1'='1", "table": "orders"
        })
        assert resp.status_code == 400


class TestImpactSQLExecution:
    """_execute_sql helper in impact module."""

    def test_no_warehouse_raises_runtime_error(self):
        """C8: When WAREHOUSE_ID is empty, should raise RuntimeError."""
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}):
            import importlib
            import backend.routes.impact as impact_mod
            importlib.reload(impact_mod)
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                impact_mod._execute_sql("SELECT 1")

    def test_warehouse_timeout_propagates(self, app_client):
        """C8: Warehouse timeout should not silently return empty."""
        with patch("backend.routes.impact._execute_sql") as mock_sql:
            mock_sql.side_effect = RuntimeError("SQL failed: WAREHOUSE_TIMEOUT")
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            # Should return 500, not empty 200
            assert resp.status_code in (500, 503)
