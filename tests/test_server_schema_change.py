"""Tests for backend.server.schema_change (capability 35 — breaking-change detection).

Covers _execute_sql, _get_current_columns, _extract_column_refs_from_expr
(keyword filtering, back-ticks), detect_breaking_changes (edge-load failure,
no edges, breaking detected, no-breaking, skip-unverifiable/malformed FQN
branches), and detect_schema_changes_for_catalog.
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import schema_change as sc


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
        with patch.object(sc, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                sc._execute_sql("SELECT 1")

    def test_success(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a"], data=[[1]]
        )
        with patch.object(sc, "WAREHOUSE_ID", "wh"), \
             patch.object(sc, "_get_client", return_value=client):
            assert sc._execute_sql("SELECT a") == [{"a": 1}]

    def test_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(sc, "WAREHOUSE_ID", "wh"), \
             patch.object(sc, "_get_client", return_value=client):
            assert sc._execute_sql("SELECT 1") == []

    def test_failed_raises(self):
        err = MagicMock()
        err.message = "x"
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=err
        )
        with patch.object(sc, "WAREHOUSE_ID", "wh"), \
             patch.object(sc, "_get_client", return_value=client):
            with pytest.raises(RuntimeError):
                sc._execute_sql("SELECT 1")


class TestGetCurrentColumns:
    def test_lowercased_set(self):
        with patch.object(sc, "_execute_sql", return_value=[{"column_name": "Amount"}, {"column_name": "ID"}]):
            assert sc._get_current_columns("c", "s", "t") == {"amount", "id"}

    def test_exception_returns_empty_set(self):
        with patch.object(sc, "_execute_sql", side_effect=RuntimeError):
            assert sc._get_current_columns("c", "s", "t") == set()


class TestExtractColumnRefs:
    def test_keywords_filtered_and_backticks(self):
        refs = sc._extract_column_refs_from_expr("SELECT `amount` FROM t WHERE amount > 0 AND status = 'x'")
        assert "amount" in refs
        assert "status" in refs
        # keyword and single-char tokens excluded
        assert "select" not in refs
        assert "from" not in refs
        assert "t" not in refs  # len <= 1

    def test_empty_expr(self):
        assert sc._extract_column_refs_from_expr("") == set()


class TestDetectBreakingChanges:
    def test_edge_load_failure(self):
        with patch.object(sc, "_execute_sql", side_effect=RuntimeError("edges down")):
            out = sc.detect_breaking_changes("c", "s", "t")
        assert "Could not load transformation edges" in out["detail"]
        assert out["breaking_changes"] == []

    def test_no_edges(self):
        with patch.object(sc, "_execute_sql", return_value=[]):
            out = sc.detect_breaking_changes("c", "s", "t")
        assert "No transformation edges" in out["detail"]

    def test_breaking_detected(self):
        edges = [
            {"src_fqn": "c.s.up", "src_col": "old_col", "dst_col": "tgt",
             "expr_sql": "old_col + 1"},
        ]
        # current schema no longer has old_col
        with patch.object(sc, "_execute_sql", return_value=edges), \
             patch.object(sc, "_get_current_columns", return_value={"new_col"}):
            out = sc.detect_breaking_changes("c", "s", "t")
        assert out["checked_upstream_tables"] == 1
        assert len(out["breaking_changes"]) == 1
        bc = out["breaking_changes"][0]
        assert bc["missing_columns"] == ["old_col"]
        assert bc["severity"] == "BREAKING"
        assert bc["affected_target_columns"] == ["tgt"]

    def test_no_breaking_when_columns_present(self):
        edges = [{"src_fqn": "c.s.up", "src_col": "keep", "dst_col": "t", "expr_sql": "keep"}]
        with patch.object(sc, "_execute_sql", return_value=edges), \
             patch.object(sc, "_get_current_columns", return_value={"keep"}):
            out = sc.detect_breaking_changes("c", "s", "t")
        assert out["breaking_changes"] == []

    def test_skips_unverifiable_upstream(self):
        edges = [{"src_fqn": "c.s.up", "src_col": "x", "dst_col": "t", "expr_sql": "x"}]
        with patch.object(sc, "_execute_sql", return_value=edges), \
             patch.object(sc, "_get_current_columns", return_value=set()):  # empty -> skip
            out = sc.detect_breaking_changes("c", "s", "t")
        assert out["breaking_changes"] == []

    def test_skips_malformed_fqn_and_blank_src(self):
        edges = [
            {"src_fqn": "", "src_col": "a", "dst_col": "t", "expr_sql": "a"},   # blank -> continue
            {"src_fqn": "badfqn", "src_col": "b", "dst_col": "t", "expr_sql": "b"},  # not 3 parts
        ]
        with patch.object(sc, "_execute_sql", return_value=edges):
            out = sc.detect_breaking_changes("c", "s", "t")
        # blank skipped entirely; badfqn present in refs but skipped in loop
        assert out["checked_upstream_tables"] == 1
        assert out["breaking_changes"] == []


class TestDetectSchemaChangesForCatalog:
    def test_enumeration_failure(self):
        with patch.object(sc, "_execute_sql", side_effect=RuntimeError):
            assert sc.detect_schema_changes_for_catalog("c") == []

    def test_only_tables_with_issues_returned(self):
        tables = [{"table_schema": "s", "table_name": "a"},
                  {"table_schema": "s", "table_name": "b"}]
        analyses = [
            {"table_full_name": "c.s.a", "breaking_changes": [{"x": 1}]},
            {"table_full_name": "c.s.b", "breaking_changes": []},
        ]
        with patch.object(sc, "_execute_sql", return_value=tables), \
             patch.object(sc, "detect_breaking_changes", side_effect=analyses):
            out = sc.detect_schema_changes_for_catalog("c", schema="s")
        assert len(out) == 1
        assert out[0]["table_full_name"] == "c.s.a"
