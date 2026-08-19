"""Unit tests for backend.server.framework_analysis.

The deep (metadata-driven framework) fallback: detect config from source (LLM),
read producer parameters, query config table(s), derive columns (LLM), emitting a
stream of commentary events then a final `result`.

Covers the pure helpers (_norm_name / _maybe_decode / _focus_on_target),
the parameter + config-table readers (JOB/PIPELINE, base64/JSON decode, target
focusing, unreadable table), and every branch of deep_analyze_stream
(no-source, detect error, no config tables, config-table skip, all-UNKNOWN
rejection, happy path with save + save-failure).
"""
import base64
import json
from unittest.mock import MagicMock, patch

import pytest

import backend.server.framework_analysis as fa


def _events(gen):
    """Drain a stream generator into a list of event dicts."""
    return list(gen)


def _result(events):
    """The single terminal `result` event (or None)."""
    return next((e for e in events if e.get("type") == "result"), None)


def _steps(events, step):
    return [e for e in events if e.get("type") == "step" and e.get("step") == step]


# ---------------------------------------------------------------------------
# _norm_name
# ---------------------------------------------------------------------------

class TestNormName:
    def test_strips_dotted_suffixes_and_backticks(self):
        assert fa._norm_name("cat.sch.Orders_target") == "orders"
        assert fa._norm_name("cat.sch.customer_dq") == "customer"
        assert fa._norm_name("`orders`") == "orders"
        assert fa._norm_name("PLAIN") == "plain"


# ---------------------------------------------------------------------------
# _maybe_decode
# ---------------------------------------------------------------------------

class TestMaybeDecode:
    def test_passthrough_non_string_and_short(self):
        assert fa._maybe_decode(5) == 5
        assert fa._maybe_decode("x") == "x"

    def test_plain_json_object(self):
        assert fa._maybe_decode('{"a": 1}') == {"a": 1}

    def test_base64_of_json(self):
        payload = base64.b64encode(json.dumps({"k": [1, 2]}).encode()).decode()
        assert fa._maybe_decode(payload) == {"k": [1, 2]}

    def test_bad_base64_falls_through_to_string(self):
        # 16-char, %4==0, base64 alphabet, but not valid base64-of-json.
        assert fa._maybe_decode("AAAAAAAAAAAAAAAA") == "AAAAAAAAAAAAAAAA"

    def test_invalid_json_string_passes_through(self):
        assert fa._maybe_decode("{not json") == "{not json"


# ---------------------------------------------------------------------------
# _focus_on_target
# ---------------------------------------------------------------------------

class TestFocusOnTarget:
    def test_prunes_list_to_matching_target(self):
        obj = {"mappings": [
            {"target_table": "orders", "cols": ["a"]},
            {"target_table": "customers", "cols": ["b"]},
        ]}
        pruned, matched = fa._focus_on_target(obj, "orders")
        assert matched is True
        assert pruned["mappings"] == [{"target_table": "orders", "cols": ["a"]}]

    def test_no_match_keeps_full_list(self):
        obj = [{"target": "other", "x": 1}]
        pruned, matched = fa._focus_on_target(obj, "orders")
        assert matched is False
        assert pruned == [{"target": "other", "x": 1}]

    def test_non_keyed_list_passthrough(self):
        pruned, matched = fa._focus_on_target([1, 2, 3], "orders")
        assert pruned == [1, 2, 3]
        assert matched is False


# ---------------------------------------------------------------------------
# _fetch_entity_parameters
# ---------------------------------------------------------------------------

class TestFetchEntityParameters:
    def test_no_client_returns_empty(self):
        with patch("backend.lineage_service._get_client", return_value=None):
            assert fa._fetch_entity_parameters("JOB", "1") == {}

    def test_pipeline_configuration(self):
        client = MagicMock()
        client.api_client.do.return_value = {"spec": {"configuration": {"env": "prod"}}}
        with patch("backend.lineage_service._get_client", return_value=client):
            out = fa._fetch_entity_parameters("PIPELINE", "p1")
        assert out == {"env": "prod"}

    def test_job_params_and_notebook_base_params(self):
        client = MagicMock()
        client.api_client.do.return_value = {"settings": {
            "parameters": [{"name": "run_date", "default": "2026-01-01"}, {"name": None}],
            "tasks": [{"notebook_task": {"base_parameters": {"mode": "full"}}}],
        }}
        with patch("backend.lineage_service._get_client", return_value=client):
            out = fa._fetch_entity_parameters("JOB", "42")
        assert out == {"run_date": "2026-01-01", "mode": "full"}

    def test_unknown_type_returns_empty(self):
        client = MagicMock()
        with patch("backend.lineage_service._get_client", return_value=client):
            assert fa._fetch_entity_parameters("NOTEBOOK", "n1") == {}

    def test_exception_returns_empty(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("boom")
        with patch("backend.lineage_service._get_client", return_value=client):
            assert fa._fetch_entity_parameters("PIPELINE", "p1") == {}


# ---------------------------------------------------------------------------
# _query_config_table
# ---------------------------------------------------------------------------

class TestQueryConfigTable:
    def test_invalid_fqn_returns_none(self):
        assert fa._query_config_table("not_fqn", "c.s.orders", []) is None

    def test_no_client_returns_none(self):
        with patch("backend.lineage_service._get_client", return_value=None):
            assert fa._query_config_table("c.s.cfg", "c.s.orders", []) is None

    def test_empty_table(self):
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=[]):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", [])
        assert out == {"table": "c.s.cfg", "columns": [], "rows": [], "total_rows": 0, "matched": False}

    def test_matched_rows_filtered_by_target(self):
        # Matching triggers when a cell holds a LIST of target-keyed dicts: the
        # list collapses to just the entry(ies) naming this target.
        rows = [{"mappings": [
            {"target_table": "orders", "expr": "a+b"},
            {"target_table": "customers", "expr": "c"},
        ]}]
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=rows):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", ["target_table"])
        assert out["matched"] is True
        assert out["rows"] == [{"mappings": [{"target_table": "orders", "expr": "a+b"}]}]
        assert out["total_rows"] == 1

    def test_base64_config_cell_is_decoded(self):
        # A cell storing base64-of-JSON gets decoded before focusing.
        blob = base64.b64encode(json.dumps(
            [{"target_table": "orders", "expr": "a"}]).encode()).decode()
        rows = [{"cfg": blob}]
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=rows):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", ["target_table"])
        assert out["matched"] is True
        assert out["rows"] == [{"cfg": [{"target_table": "orders", "expr": "a"}]}]

    def test_no_match_returns_sample(self):
        rows = [{"other": i} for i in range(20)]
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=rows):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", [])
        assert out["matched"] is False
        assert len(out["rows"]) == 10  # sample cap


# ---------------------------------------------------------------------------
# deep_analyze_stream
# ---------------------------------------------------------------------------

class TestDeepAnalyzeStream:
    def test_no_source(self):
        with patch.object(fa, "_fetch_source", return_value="   "):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "fetch_source")[0]["status"] == "error"
        r = _result(events)
        assert r["derived"] is False and r["columns"] == []

    def test_detect_config_error(self):
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config",
                          return_value={"error": "llm down"}):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "detect_config")[-1]["status"] == "error"
        assert _result(events)["derived"] is False

    def test_no_columns_derived_is_rejected(self):
        cfg = {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": ""}
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config",
                          return_value=[{"target_column": "x", "category": "UNKNOWN"}]):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "query_config")[0]["status"] == "warn"  # no config tables
        assert _steps(events, "derive")[-1]["status"] == "error"
        assert _result(events)["derived"] is False

    def test_happy_path_with_config_table_and_save(self):
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": True}],
               "parameters": ["run_date"], "target_key_columns": ["target_table"], "notes": "maps cols"}
        good_cols = [{"target_column": "amount_usd", "source_columns": ["amount"],
                      "expression": "amount*fx", "category": "ARITHMETIC"}]
        with patch.object(fa, "_fetch_source", return_value="framework code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={"run_date": "2026-01-01"}), \
             patch.object(fa, "_query_config_table",
                          return_value={"table": "c.s.cfg", "columns": ["target_table"],
                                        "rows": [{"target_table": "t"}], "total_rows": 5, "matched": True}), \
             patch.object(fa, "_fetch_target_columns", return_value=["amount_usd"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", return_value=9):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t", actor="me", model="m1"))
        assert _steps(events, "query_config")[-1]["status"] == "ok"
        assert _steps(events, "save")[0]["status"] == "ok"
        r = _result(events)
        assert r["derived"] is True
        assert r["columns"] == good_cols
        assert r["version"] == 9
        assert r["config_tables"] == ["c.s.cfg"]

    def test_config_table_query_raises_is_skipped(self):
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": True}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        good_cols = [{"target_column": "x", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", side_effect=RuntimeError("no perms")), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", return_value=1):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "query_config")[-1]["status"] == "warn"
        assert _result(events)["derived"] is True

    def test_config_table_invalid_returns_none_skipped(self):
        cfg = {"config_tables": [{"name": "c.s.cfg"}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        good_cols = [{"target_column": "x", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value=None), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", return_value=2):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        warns = _steps(events, "query_config")
        assert any(w["status"] == "warn" for w in warns)
        assert _result(events)["derived"] is True

    def test_save_failure_still_returns_columns(self):
        cfg = {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": ""}
        good_cols = [{"target_column": "x", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", side_effect=RuntimeError("write failed")):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "save")[0]["status"] == "warn"
        r = _result(events)
        assert r["derived"] is True
        assert r["version"] is None
