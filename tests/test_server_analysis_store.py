"""Unit tests for backend.server.analysis_store.

Covers:
- _execute_sql (no-warehouse, success, failed, empty)
- _source_hash, _sql_str, _key_where helpers
- _ensure_table (cached, create+migrate, migrate-fails swallowed, create error)
- _decode_row (good + malformed json)
- get_cached_analysis (hit, miss/none, error)
- get_latest_version / get_version (hit, none, error)
- save_analysis (version increment, first-version fallback, insert error)
- list_versions (happy, error)
- list_analyses (filters, no filters, error)
"""
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState

import backend.server.analysis_store as store


@pytest.fixture(autouse=True)
def _reset_ready():
    store._table_ready = False
    yield
    store._table_ready = False


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


class TestExecuteSql:
    def test_no_warehouse(self):
        with patch.object(store, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                store._execute_sql("SELECT 1")

    def test_success(self):
        client = _make_client(columns=["a"], data=[["v"]])
        with patch.object(store, "WAREHOUSE_ID", "wh"), patch.object(store, "_get_client", return_value=client):
            assert store._execute_sql("SELECT a") == [{"a": "v"}]

    def test_failed(self):
        client = _make_client(state=StatementState.FAILED, error_msg="e")
        with patch.object(store, "WAREHOUSE_ID", "wh"), patch.object(store, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="e"):
                store._execute_sql("SELECT 1")

    def test_empty(self):
        client = _make_client(data=None)
        with patch.object(store, "WAREHOUSE_ID", "wh"), patch.object(store, "_get_client", return_value=client):
            assert store._execute_sql("SELECT 1") == []


class TestHelpers:
    def test_source_hash_deterministic_16hex(self):
        h = store._source_hash("abc")
        assert len(h) == 16
        assert h == store._source_hash("abc")

    def test_sql_str_escapes(self):
        assert store._sql_str("a'b\\c") == "a''b\\\\c"
        assert store._sql_str(None) == ""

    def test_sql_str_delegates_to_shared_helper(self):
        """The local _sql_str is now a thin alias for backend.validators.sql_str —
        one escaper for the whole app instead of a per-module copy."""
        from backend.validators import sql_str
        for value in ("a'b\\c", None, "", "plain", "\\' OR 1=1--"):
            assert store._sql_str(value) == sql_str(value)

    def test_sql_str_escapes_backslash_before_quote(self):
        r"""Backslash FIRST: a leading `\'` must become `\\''`, not `\''` — the
        latter's second quote closes the literal on Databricks SQL."""
        assert store._sql_str("\\' OR 1=1--") == "\\\\'' OR 1=1--"

    def test_key_where_escapes_backslash_quote(self):
        w = store._key_where("JOB", "\\' OR 1=1--", "c.s.t")
        assert "entity_id = '\\\\'' OR 1=1--'" in w
        assert "entity_id = '\\''" not in w

    def test_key_where(self):
        w = store._key_where("JOB", "1", "c.s.t")
        assert "entity_type = 'JOB'" in w
        assert "target_table = 'c.s.t'" in w


class TestEnsureTable:
    def test_cached_short_circuit(self):
        store._table_ready = True
        with patch.object(store, "_execute_sql") as m:
            store._ensure_table()
        m.assert_not_called()

    def test_create_and_migrate(self):
        with patch.object(store, "_execute_sql", return_value=[]) as m:
            store._ensure_table()
        assert store._table_ready is True
        assert m.call_count == 2  # create + add columns

    def test_migrate_error_swallowed_still_ready(self):
        calls = {"n": 0}

        def fake(sql):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("already present")
            return []

        with patch.object(store, "_execute_sql", side_effect=fake):
            store._ensure_table()
        assert store._table_ready is True

    def test_create_error_not_ready(self):
        with patch.object(store, "_execute_sql", side_effect=RuntimeError("no perms")):
            store._ensure_table()
        assert store._table_ready is False


class TestDecodeRow:
    def test_good(self):
        r = {"entity_type": "JOB", "entity_id": "1", "analysis_json": '[{"c": 1}]',
             "version": "5", "analyzed_at": "t"}
        out = store._decode_row(r)
        assert out["columns"] == [{"c": 1}]
        assert out["version"] == 5

    def test_malformed_json(self):
        out = store._decode_row({"analysis_json": "not-json"})
        assert out["columns"] == []
        assert out["version"] == 0


class TestGetCachedAnalysis:
    def test_hit(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[{"analysis_json": '[{"c": 1}]'}]):
            out = store.get_cached_analysis("JOB", "1", "c.s.t", "src")
        assert out == [{"c": 1}]

    def test_miss_empty(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[]):
            assert store.get_cached_analysis("JOB", "1", "c.s.t", "src") is None

    def test_error(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", side_effect=RuntimeError("x")):
            assert store.get_cached_analysis("JOB", "1", "c.s.t", "src") is None


class TestGetLatestVersion:
    def test_hit(self):
        row = {"entity_type": "JOB", "entity_id": "1", "analysis_json": "[]", "version": "2"}
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[row]):
            out = store.get_latest_version("JOB", "1", "c.s.t")
        assert out["version"] == 2

    def test_none(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[]):
            assert store.get_latest_version("JOB", "1", "c.s.t") is None

    def test_error(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", side_effect=RuntimeError("x")):
            assert store.get_latest_version("JOB", "1", "c.s.t") is None


class TestGetVersion:
    def test_hit(self):
        row = {"entity_type": "JOB", "analysis_json": "[]", "version": "3"}
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[row]) as m:
            out = store.get_version("JOB", "1", "c.s.t", 3)
        assert out["version"] == 3
        assert "version = 3" in m.call_args[0][0]

    def test_none(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[]):
            assert store.get_version("JOB", "1", "c.s.t", 3) is None

    def test_error(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", side_effect=RuntimeError("x")):
            assert store.get_version("JOB", "1", "c.s.t", 3) is None


class TestSaveAnalysis:
    def test_version_increment(self):
        calls = []

        def fake(sql):
            calls.append(sql)
            if "MAX(version)" in sql:
                return [{"max_ver": 4}]
            return []

        with patch.object(store, "_ensure_table"), patch.object(store, "_execute_sql", side_effect=fake):
            ver = store.save_analysis("JOB", "1", "src", "c.s.t", [{"c": 1}], "model", "actor")
        assert ver == 5
        assert any("INSERT INTO" in c for c in calls)

    def test_first_version_when_max_query_fails(self):
        def fake(sql):
            if "MAX(version)" in sql:
                raise RuntimeError("x")
            return []

        with patch.object(store, "_ensure_table"), patch.object(store, "_execute_sql", side_effect=fake):
            ver = store.save_analysis("JOB", "1", "src", "c.s.t", [], "m", "a")
        assert ver == 1

    def test_no_prior_rows_defaults_to_one(self):
        def fake(sql):
            if "MAX(version)" in sql:
                return []
            return []

        with patch.object(store, "_ensure_table"), patch.object(store, "_execute_sql", side_effect=fake):
            ver = store.save_analysis("JOB", "1", "src", "c.s.t", [], "m", "a")
        assert ver == 1

    def test_insert_error_swallowed_returns_version(self):
        def fake(sql):
            if "MAX(version)" in sql:
                return [{"max_ver": 0}]
            raise RuntimeError("insert fail")

        with patch.object(store, "_ensure_table"), patch.object(store, "_execute_sql", side_effect=fake):
            ver = store.save_analysis("JOB", "1", "src", "c.s.t", [], "m", "a")
        assert ver == 1


class TestListVersions:
    def test_happy(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[{"version": 2}]) as m:
            out = store.list_versions("JOB", "1", "c.s.t", limit=10)
        assert out == [{"version": 2}]
        assert "LIMIT 10" in m.call_args[0][0]

    def test_error(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", side_effect=RuntimeError("x")):
            assert store.list_versions("JOB", "1", "c.s.t") == []


class TestListAnalyses:
    def test_with_both_filters(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[{"version": 1}]) as m:
            out = store.list_analyses("JOB", "1")
        assert out == [{"version": 1}]
        sql = m.call_args[0][0]
        assert "WHERE" in sql and "entity_type = 'JOB'" in sql and "entity_id = '1'" in sql

    def test_no_filters(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", return_value=[]) as m:
            store.list_analyses()
        assert "WHERE" not in m.call_args[0][0]

    def test_error(self):
        with patch.object(store, "_ensure_table"), \
             patch.object(store, "_execute_sql", side_effect=RuntimeError("x")):
            assert store.list_analyses() == []
