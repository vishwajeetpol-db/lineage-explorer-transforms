"""Tests for backend.server.discovery (capability 22 — search & discovery).

Covers _execute_sql, search_assets (with catalog/type filters + exception),
find_sensitive_tables (scope-guard, heuristic grouping, exception), and
find_orphan_tables (scope-guard, mapping, exception).
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import discovery


def _col(name):
    c = MagicMock()
    c.name = name
    return c


def _resp(state=StatementState.SUCCEEDED, columns=None, data=None, error=None):
    resp = MagicMock()
    resp.status.state = state
    resp.status.error = error
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        resp.manifest.schema.columns = [_col(c) for c in (columns or [])]
    return resp


class TestExecuteSql:
    def test_no_warehouse_raises(self):
        with patch.object(discovery, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                discovery._execute_sql("SELECT 1")

    def test_success(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a"], data=[[1]]
        )
        with patch.object(discovery, "WAREHOUSE_ID", "wh"), \
             patch.object(discovery, "_get_client", return_value=client):
            assert discovery._execute_sql("SELECT a") == [{"a": 1}]

    def test_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(discovery, "WAREHOUSE_ID", "wh"), \
             patch.object(discovery, "_get_client", return_value=client):
            assert discovery._execute_sql("SELECT 1") == []

    def test_failed_raises(self):
        err = MagicMock()
        err.message = "bad"
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=err
        )
        with patch.object(discovery, "WAREHOUSE_ID", "wh"), \
             patch.object(discovery, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="bad"):
                discovery._execute_sql("SELECT 1")


class TestSearchAssets:
    def test_maps_rows_no_filters(self):
        rows = [{"table_catalog": "c", "table_schema": "s", "table_name": "t",
                 "table_type": "MANAGED", "table_owner": "o", "comment": "cm",
                 "created": "2020"}]
        with patch.object(discovery, "_execute_sql", return_value=rows) as m:
            out = discovery.search_assets("orders")
        assert out[0]["full_name"] == "c.s.t"
        assert out[0]["type"] == "MANAGED"
        # no filters injected
        assert "table_catalog IN" not in m.call_args[0][0]

    def test_applies_catalog_and_type_filters(self):
        with patch.object(discovery, "_execute_sql", return_value=[]) as m:
            discovery.search_assets("q", catalogs=["main"], asset_types=["view"])
        sql = m.call_args[0][0]
        assert "table_catalog IN ('main' )" in sql
        assert "table_type IN ('VIEW' )" in sql

    def test_exception_returns_empty(self):
        with patch.object(discovery, "_execute_sql", side_effect=RuntimeError):
            assert discovery.search_assets("q") == []


class TestFindSensitiveTables:
    def test_scope_guard_returns_empty(self):
        # No _execute_sql should even be called
        with patch.object(discovery, "_execute_sql") as m:
            assert discovery.find_sensitive_tables() == []
        m.assert_not_called()

    def test_groups_sensitive_columns(self):
        rows = [
            {"table_catalog": "c", "table_schema": "s", "table_name": "users", "column_name": "email"},
            {"table_catalog": "c", "table_schema": "s", "table_name": "users", "column_name": "phone"},
            {"table_catalog": "c", "table_schema": "s", "table_name": "users", "column_name": "id"},
            {"table_catalog": "c", "table_schema": "s", "table_name": "pay", "column_name": "card"},
        ]
        with patch.object(discovery, "_execute_sql", return_value=rows):
            out = discovery.find_sensitive_tables(catalog="c")
        by_name = {t["name"]: t for t in out}
        assert set(by_name) == {"users", "pay"}
        assert len(by_name["users"]["sensitive_columns"]) == 2  # email, phone
        assert by_name["pay"]["sensitive_columns"][0]["sensitivity"] == "PCI"

    def test_schema_only_scope_ok(self):
        with patch.object(discovery, "_execute_sql", return_value=[]) as m:
            assert discovery.find_sensitive_tables(schema="s") == []
        assert "table_schema = 's'" in m.call_args[0][0]

    def test_exception_returns_empty(self):
        with patch.object(discovery, "_execute_sql", side_effect=RuntimeError):
            assert discovery.find_sensitive_tables(catalog="c") == []


class TestFindOrphanTables:
    def test_scope_guard_returns_empty(self):
        with patch.object(discovery, "_execute_sql") as m:
            assert discovery.find_orphan_tables() == []
        m.assert_not_called()

    def test_maps_rows(self):
        rows = [{"table_catalog": "c", "table_schema": "s", "table_name": "stale",
                 "table_owner": "o"}]
        with patch.object(discovery, "_execute_sql", return_value=rows):
            out = discovery.find_orphan_tables(catalog="c")
        assert out[0]["full_name"] == "c.s.stale"
        assert out[0]["owner"] == "o"

    def test_schema_only_scope(self):
        with patch.object(discovery, "_execute_sql", return_value=[]) as m:
            discovery.find_orphan_tables(schema="s")
        assert "t.table_schema = 's'" in m.call_args[0][0]

    def test_exception_returns_empty(self):
        with patch.object(discovery, "_execute_sql", side_effect=RuntimeError):
            assert discovery.find_orphan_tables(catalog="c") == []
