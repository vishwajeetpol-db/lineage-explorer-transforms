"""Tests for backend.server.column_profiling (capability 36 — profiling overlay).

Covers _execute_sql, _get_delta_stats (row-count + column_statistics, both
exception branches), _get_live_profile (provided cols, auto-discovered cols,
empty-cols short-circuit, null_pct math, exception), and get_column_profile
(delta-only, live-merge, live-empty-fallback branches).
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import column_profiling as cp


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
        with patch.object(cp, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                cp._execute_sql("SELECT 1")

    def test_success(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a"], data=[[1]]
        )
        with patch.object(cp, "WAREHOUSE_ID", "wh"), \
             patch.object(cp, "_get_client", return_value=client):
            assert cp._execute_sql("SELECT a") == [{"a": 1}]

    def test_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(cp, "WAREHOUSE_ID", "wh"), \
             patch.object(cp, "_get_client", return_value=client):
            assert cp._execute_sql("SELECT 1") == []

    def test_failed_raises(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=None
        )
        with patch.object(cp, "WAREHOUSE_ID", "wh"), \
             patch.object(cp, "_get_client", return_value=client):
            with pytest.raises(RuntimeError):
                cp._execute_sql("SELECT 1")


class TestGetDeltaStats:
    def _router(self, describe=None, colstats=None, describe_exc=False, colstats_exc=False):
        def router(sql):
            if "DESCRIBE EXTENDED" in sql:
                if describe_exc:
                    raise RuntimeError("describe")
                return describe or []
            if "column_statistics" in sql:
                if colstats_exc:
                    raise RuntimeError("colstats")
                return colstats or []
            return []
        return router

    def test_row_count_and_column_stats(self):
        describe = [
            {"col_name": "id", "data_type": "bigint"},
            {"col_name": "Statistics", "data_type": "1000 rows"},
        ]
        colstats = [{"column_name": "id", "null_count": 0, "distinct_count": 1000,
                     "avg_col_len": 8, "max_col_len": 8}]
        with patch.object(cp, "_execute_sql",
                          side_effect=self._router(describe=describe, colstats=colstats)):
            out = cp._get_delta_stats("c", "s", "t")
        assert out["row_count_approx"] == "1000 rows"
        assert out["columns"][0]["distinct_count"] == 1000
        assert out["columns"][0]["profile_source"] == "information_schema.column_statistics"

    def test_both_exceptions_soft(self):
        with patch.object(cp, "_execute_sql",
                          side_effect=self._router(describe_exc=True, colstats_exc=True)):
            out = cp._get_delta_stats("c", "s", "t")
        assert out["columns"] == []
        assert "row_count_approx" not in out


class TestGetLiveProfile:
    def test_with_provided_columns(self):
        row = {"__total_rows": "100", "amount__distinct": "40", "amount__null_count": "10"}
        with patch.object(cp, "_execute_sql", return_value=[row]):
            out = cp._get_live_profile("c", "s", "t", columns=["amount"])
        assert out[0]["distinct_count"] == 40
        assert out[0]["null_count"] == 10
        assert out[0]["null_pct"] == 10.0
        assert out[0]["profile_source"] == "live_query"

    def test_auto_discovers_columns(self):
        def router(sql):
            if "information_schema.columns" in sql:
                return [{"column_name": "x", "data_type": "int"}]
            return [{"__total_rows": "0", "x__distinct": "0", "x__null_count": "0"}]
        with patch.object(cp, "_execute_sql", side_effect=router):
            out = cp._get_live_profile("c", "s", "t")
        assert out[0]["name"] == "x"
        assert out[0]["null_pct"] is None  # total==0 branch

    def test_column_discovery_exception_returns_empty(self):
        def router(sql):
            if "information_schema.columns" in sql:
                raise RuntimeError("discover")
            return []
        with patch.object(cp, "_execute_sql", side_effect=router):
            assert cp._get_live_profile("c", "s", "t") == []

    def test_no_columns_discovered_returns_empty(self):
        def router(sql):
            if "information_schema.columns" in sql:
                return []
            return []
        with patch.object(cp, "_execute_sql", side_effect=router):
            assert cp._get_live_profile("c", "s", "t") == []

    def test_profile_query_empty_result(self):
        with patch.object(cp, "_execute_sql", return_value=[]):
            assert cp._get_live_profile("c", "s", "t", columns=["a"]) == []

    def test_profile_query_exception(self):
        with patch.object(cp, "_execute_sql", side_effect=RuntimeError):
            assert cp._get_live_profile("c", "s", "t", columns=["a"]) == []


class TestGetColumnProfile:
    def test_delta_only(self):
        stats = {"source": "delta_stats", "columns": [{"name": "id"}],
                 "row_count_approx": "500 rows"}
        with patch.object(cp, "_get_delta_stats", return_value=stats):
            out = cp.get_column_profile("c", "s", "t")
        assert out["profile_source"] == "delta_stats"
        assert out["row_count_approx"] == "500 rows"
        assert out["columns"] == [{"name": "id"}]

    def test_delta_only_none_when_no_columns(self):
        with patch.object(cp, "_get_delta_stats", return_value={"columns": []}):
            out = cp.get_column_profile("c", "s", "t")
        assert out["profile_source"] == "none"

    def test_live_merges_over_delta(self):
        delta = {"columns": [{"name": "amount", "avg_col_len": 8}]}
        live = [{"name": "amount", "distinct_count": 5, "null_pct": 0.0}]
        with patch.object(cp, "_get_delta_stats", return_value=delta), \
             patch.object(cp, "_get_live_profile", return_value=live):
            out = cp.get_column_profile("c", "s", "t", live_profile=True)
        assert out["profile_source"] == "live_query"
        merged = out["columns"][0]
        assert merged["avg_col_len"] == 8      # from delta
        assert merged["distinct_count"] == 5   # from live

    def test_live_empty_falls_back_to_delta(self):
        delta = {"columns": [{"name": "amount"}]}
        with patch.object(cp, "_get_delta_stats", return_value=delta), \
             patch.object(cp, "_get_live_profile", return_value=[]):
            out = cp.get_column_profile("c", "s", "t", live_profile=True)
        assert out["profile_source"] == "delta_stats"
        assert out["columns"] == [{"name": "amount"}]

    def test_live_empty_and_no_delta_is_none(self):
        with patch.object(cp, "_get_delta_stats", return_value={"columns": []}), \
             patch.object(cp, "_get_live_profile", return_value=[]):
            out = cp.get_column_profile("c", "s", "t", live_profile=True)
        assert out["profile_source"] == "none"
