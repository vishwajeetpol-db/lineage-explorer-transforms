"""Tests for backend.server.scd_lineage (capability 34 — SCD/CDC lineage).

Covers _execute_sql, get_cdc_spec (JSON-column parsing, annotation building for
keys/sequence_by/scd_type/behaviors/column-scope, empty rows -> None, exception
-> None), and list_cdc_targets.
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import scd_lineage


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
        with patch.object(scd_lineage, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                scd_lineage._execute_sql("SELECT 1")

    def test_success(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a"], data=[[1]]
        )
        with patch.object(scd_lineage, "WAREHOUSE_ID", "wh"), \
             patch.object(scd_lineage, "_get_client", return_value=client):
            assert scd_lineage._execute_sql("SELECT a") == [{"a": 1}]

    def test_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(scd_lineage, "WAREHOUSE_ID", "wh"), \
             patch.object(scd_lineage, "_get_client", return_value=client):
            assert scd_lineage._execute_sql("SELECT 1") == []

    def test_failed_raises(self):
        err = MagicMock()
        err.message = "x"
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=err
        )
        with patch.object(scd_lineage, "WAREHOUSE_ID", "wh"), \
             patch.object(scd_lineage, "_get_client", return_value=client):
            with pytest.raises(RuntimeError):
                scd_lineage._execute_sql("SELECT 1")


class TestGetCdcSpec:
    def test_full_spec_with_all_annotations(self):
        row = {
            "source_full_name": "c.s.src",
            "keys": '["id", "region"]',           # JSON string -> parsed
            "scd_type": "2",
            "sequence_by": "ts",
            "column_list": ["a", "b"],             # already a list
            "except_column_list": '["c"]',
            "ignore_null_updates": True,
            "apply_as_deletes": "op = 'DELETE'",
            "apply_as_truncates": None,
            "captured_at": "2026-01-01",
            "version": "3",
        }
        with patch.object(scd_lineage, "_execute_sql", return_value=[row]):
            out = scd_lineage.get_cdc_spec("c", "s", "t")
        assert out["target"] == "c.s.t"
        assert out["keys"] == ["id", "region"]
        assert out["scd_type"] == 2
        assert out["stored_as"] == "SCD_TYPE_2"
        assert out["version"] == 3
        types = {a["type"] for a in out["graph_annotations"]}
        assert types == {"key", "sequence_by", "scd_type", "behavior",
                         "column_scope", "column_scope_except"}
        key_ann = next(a for a in out["graph_annotations"] if a["type"] == "key")
        assert "keys" in key_ann["label"]  # plural label branch

    def test_single_key_singular_label_and_minimal(self):
        row = {"source_full_name": None, "keys": '["id"]', "scd_type": None,
               "sequence_by": None, "column_list": None, "except_column_list": None}
        with patch.object(scd_lineage, "_execute_sql", return_value=[row]):
            out = scd_lineage.get_cdc_spec("c", "s", "t")
        assert out["scd_type"] == 1  # default
        key_ann = next(a for a in out["graph_annotations"] if a["type"] == "key")
        assert "key:" in key_ann["label"]  # singular
        # only key + scd_type annotations present
        types = [a["type"] for a in out["graph_annotations"]]
        assert types == ["key", "scd_type"]

    def test_bad_json_wraps_in_list(self):
        row = {"keys": "not-json", "scd_type": "1", "sequence_by": ""}
        with patch.object(scd_lineage, "_execute_sql", return_value=[row]):
            out = scd_lineage.get_cdc_spec("c", "s", "t")
        assert out["keys"] == ["not-json"]

    def test_no_rows_returns_none(self):
        with patch.object(scd_lineage, "_execute_sql", return_value=[]):
            assert scd_lineage.get_cdc_spec("c", "s", "t") is None

    def test_exception_returns_none(self):
        with patch.object(scd_lineage, "_execute_sql", side_effect=RuntimeError):
            assert scd_lineage.get_cdc_spec("c", "s", "t") is None


class TestListCdcTargets:
    def test_happy(self):
        rows = [{"target_full_name": "c.s.t", "scd_type": 2, "latest_version": 4}]
        with patch.object(scd_lineage, "_execute_sql", return_value=rows):
            assert scd_lineage.list_cdc_targets() == rows

    def test_exception_returns_empty(self):
        with patch.object(scd_lineage, "_execute_sql", side_effect=RuntimeError):
            assert scd_lineage.list_cdc_targets() == []
