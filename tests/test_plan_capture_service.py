"""Unit tests for backend.plan_capture_service.

Covers:
- _execute_sql (no-warehouse, success, failed, empty)
- get_plan_capture_status (flag off, plans reachable, plans error, cdc count, cdc error)
- get_captured_expression (flags off, no rows, no match, match, error)
- get_captured_columns (flag off, no rows, empty cols, happy, error)
- list_captured_versions (flag off, happy, error)
- get_captured_columns_version (flag off, no rows, happy, error)
- get_captured_cdc_spec (flag off, no rows, happy, error)
"""
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState

import backend.plan_capture_service as pcs


def _make_client(columns=None, data=None, state=StatementState.SUCCEEDED, error_msg=None):
    resp = MagicMock()
    resp.status.state = state
    if error_msg is not None:
        resp.status.error.message = error_msg
    else:
        resp.status.error = None
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        cols = []
        for name in (columns or []):
            c = MagicMock()
            c.name = name
            cols.append(c)
        resp.manifest.schema.columns = cols
    client = MagicMock()
    client.statement_execution.execute_statement.return_value = resp
    return client


def _flags(on):
    """Return a get_flag_state stub returning `on` for every flag."""
    return lambda flag: on


class TestExecuteSql:
    def test_no_warehouse(self):
        with patch.object(pcs, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                pcs._execute_sql("SELECT 1")

    def test_success(self):
        client = _make_client(columns=["a"], data=[["v"]])
        with patch.object(pcs, "WAREHOUSE_ID", "wh"), patch.object(pcs, "_get_client", return_value=client):
            assert pcs._execute_sql("SELECT a") == [{"a": "v"}]

    def test_failed(self):
        client = _make_client(state=StatementState.FAILED, error_msg="e")
        with patch.object(pcs, "WAREHOUSE_ID", "wh"), patch.object(pcs, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="e"):
                pcs._execute_sql("SELECT 1")

    def test_empty(self):
        client = _make_client(data=None)
        with patch.object(pcs, "WAREHOUSE_ID", "wh"), patch.object(pcs, "_get_client", return_value=client):
            assert pcs._execute_sql("SELECT 1") == []


class TestGetPlanCaptureStatus:
    def test_flag_off(self):
        with patch.object(pcs, "get_flag_state", return_value=False):
            out = pcs.get_plan_capture_status()
        assert out["enabled"] is False
        assert out["table_reachable"] is False

    def test_reachable_with_counts(self):
        def fake_sql(sql):
            if "count(DISTINCT target_full_name)" in sql:
                return [{"n": 12, "t": 3}]
            return [{"n": 5}]  # cdc

        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=fake_sql):
            out = pcs.get_plan_capture_status()
        assert out["captured_plan_count"] == 12
        assert out["distinct_targets"] == 3
        assert out["table_reachable"] is True
        assert out["captured_cdc_spec_count"] == 5

    def test_plans_error_then_cdc_error(self):
        def fake_sql(sql):
            raise RuntimeError("unreachable")

        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=fake_sql):
            out = pcs.get_plan_capture_status()
        assert out["table_reachable"] is False
        assert out["captured_cdc_spec_count"] == 0


class TestGetCapturedExpression:
    def test_flags_off(self):
        # Requires BOTH plan_capture AND precedence flag.
        with patch.object(pcs, "get_flag_state", side_effect=lambda f: f == pcs.PLAN_CAPTURE_FLAG):
            assert pcs.get_captured_expression("c", "s", "t", "col") is None

    def test_no_rows(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[]):
            assert pcs.get_captured_expression("c", "s", "t", "col") is None

    def test_no_match(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[{"analyzed_plan": "P"}]), \
             patch.object(pcs, "parse_plan", return_value=[{"target_column": "other"}]):
            assert pcs.get_captured_expression("c", "s", "t", "col") is None

    def test_match(self):
        rows = [{"analyzed_plan": "P", "version": 2, "captured_via": "wheel", "captured_at": "2026"}]
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=rows), \
             patch.object(pcs, "parse_plan", return_value=[{"target_column": "col", "expression": "x"}]):
            out = pcs.get_captured_expression("c", "s", "t", "col")
        assert out["target_column"] == "col"
        assert out["captured_via"] == "wheel"
        assert out["version"] == 2

    def test_error(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=RuntimeError("x")):
            assert pcs.get_captured_expression("c", "s", "t", "col") is None


class TestGetCapturedColumns:
    def test_flag_off(self):
        with patch.object(pcs, "get_flag_state", return_value=False):
            assert pcs.get_captured_columns("c", "s", "t") is None

    def test_no_rows(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[]):
            assert pcs.get_captured_columns("c", "s", "t") is None

    def test_empty_analyzed_plan(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[{"analyzed_plan": None}]):
            assert pcs.get_captured_columns("c", "s", "t") is None

    def test_parse_returns_empty(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[{"analyzed_plan": "P"}]), \
             patch.object(pcs, "parse_plan", return_value=[]):
            assert pcs.get_captured_columns("c", "s", "t") is None

    def test_happy(self):
        rows = [{"analyzed_plan": "P", "version": 3, "captured_via": "w", "captured_at": "2026"}]
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=rows), \
             patch.object(pcs, "parse_plan", return_value=[{"target_column": "a"}]):
            out = pcs.get_captured_columns("c", "s", "t")
        assert out["version"] == 3
        assert out["columns"] == [{"target_column": "a"}]

    def test_error(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=RuntimeError("x")):
            assert pcs.get_captured_columns("c", "s", "t") is None


class TestListCapturedVersions:
    def test_flag_off(self):
        with patch.object(pcs, "get_flag_state", return_value=False):
            assert pcs.list_captured_versions("c", "s", "t") == []

    def test_happy(self):
        rows = [{"version": 2, "captured_via": "w", "captured_at": "2026", "plan_hash": "h"}]
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=rows):
            out = pcs.list_captured_versions("c", "s", "t", limit=5)
        assert out[0]["ref"] == "plan_capture:2"
        assert out[0]["source"] == "plan_capture"
        assert out[0]["hash"] == "h"

    def test_error(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=RuntimeError("x")):
            assert pcs.list_captured_versions("c", "s", "t") == []


class TestGetCapturedColumnsVersion:
    def test_flag_off(self):
        with patch.object(pcs, "get_flag_state", return_value=False):
            assert pcs.get_captured_columns_version("c", "s", "t", 1) is None

    def test_no_rows(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[]):
            assert pcs.get_captured_columns_version("c", "s", "t", 1) is None

    def test_empty_plan(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[{"analyzed_plan": ""}]):
            assert pcs.get_captured_columns_version("c", "s", "t", 1) is None

    def test_happy(self):
        rows = [{"analyzed_plan": "P", "version": 1, "captured_via": "w", "captured_at": "2026"}]
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=rows), \
             patch.object(pcs, "parse_plan", return_value=[{"target_column": "a"}]):
            out = pcs.get_captured_columns_version("c", "s", "t", 1)
        assert out["columns"] == [{"target_column": "a"}]

    def test_error(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=RuntimeError("x")):
            assert pcs.get_captured_columns_version("c", "s", "t", 1) is None


class TestGetCapturedCdcSpec:
    def test_flag_off(self):
        with patch.object(pcs, "get_flag_state", return_value=False):
            assert pcs.get_captured_cdc_spec("c", "s", "t") is None

    def test_no_rows(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=[]):
            assert pcs.get_captured_cdc_spec("c", "s", "t") is None

    def test_happy_source_fallback(self):
        rows = [{"version": 1, "keys": "k", "sequence_by": "ts", "scd_type": 2,
                 "source_full_name": None, "source": "src", "captured_at": "2026"}]
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", return_value=rows):
            out = pcs.get_captured_cdc_spec("c", "s", "t")
        assert out["scd_type"] == 2
        assert out["source"] == "src"

    def test_error(self):
        with patch.object(pcs, "get_flag_state", return_value=True), \
             patch.object(pcs, "_execute_sql", side_effect=RuntimeError("x")):
            assert pcs.get_captured_cdc_spec("c", "s", "t") is None
