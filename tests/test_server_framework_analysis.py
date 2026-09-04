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

    def test_detected_key_column_is_honoured(self):
        # A framework keying its config by a NON-standard column (`tgt_tbl`) must
        # still match — the detected target_key_columns are what make this work.
        # Without them the row set falls back to a blind sample (matched=False) and
        # derivation reports "no columns" for config that was right there.
        rows = [{"mappings": [
            {"tgt_tbl": "orders", "expr": "a+b"},
            {"tgt_tbl": "customers", "expr": "c"},
        ]}]
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=rows):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", ["tgt_tbl"])
        assert out["matched"] is True
        assert out["rows"] == [{"mappings": [{"tgt_tbl": "orders", "expr": "a+b"}]}]

    def test_nonstandard_key_without_detection_does_not_match(self):
        # Same data, but the detection didn't report the key → no match (documents
        # exactly what the key_columns wiring buys).
        rows = [{"mappings": [{"tgt_tbl": "orders", "expr": "a+b"}]}]
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", return_value=rows):
            out = fa._query_config_table("c.s.cfg", "c.s.orders", [])
        assert out["matched"] is False


# ---------------------------------------------------------------------------
# _target_keys / _entity_label / _blockers_note
# ---------------------------------------------------------------------------

class TestTargetKeys:
    def test_detected_keys_come_first_then_defaults(self):
        keys = fa._target_keys(["tgt_tbl"])
        assert keys[0] == "tgt_tbl"
        assert set(fa._TARGET_KEYS).issubset(set(keys))

    def test_dedupes_and_drops_blanks_and_non_strings(self):
        keys = fa._target_keys(["target", " tgt ", "", None, 7, "tgt"])
        assert keys.count("target") == 1
        assert keys.count("tgt") == 1      # " tgt " stripped, then deduped
        assert None not in keys and 7 not in keys

    def test_none_yields_defaults(self):
        assert fa._target_keys(None) == fa._TARGET_KEYS


class TestEntityLabel:
    def test_known_types_get_friendly_nouns(self):
        assert fa._entity_label("MATERIALIZED_VIEW") == "materialized view"
        assert fa._entity_label("PIPELINE") == "pipeline"
        assert fa._entity_label("SQL_TASK") == "SQL task"

    def test_unknown_type_is_humanized_not_raw(self):
        assert fa._entity_label("SOME_NEW_KIND") == "some new kind"

    def test_missing_type_degrades_to_producer(self):
        assert fa._entity_label("") == "producer"
        assert fa._entity_label(None) == "producer"


class TestBlockersNote:
    def test_empty_when_nothing_blocked(self):
        assert fa._blockers_note([], [], []) == ""

    def test_names_each_cause_distinctly(self):
        note = fa._blockers_note(["c.s.e"], ["c.s.u"], ["c.s.g"])
        assert "SELECT" in note and "c.s.u" in note      # grant problem
        assert "empty" in note and "c.s.e" in note       # re-run problem
        assert "inferred" in note and "c.s.g" in note    # guessed name


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

    def test_empty_config_table_gives_specific_reason(self):
        # Config table identified but currently empty (total_rows == 0) → short-circuit
        # with a specific config_empty reason, WITHOUT calling the derive LLM.
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": True}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.cfg", "columns": [], "rows": [], "total_rows": 0, "matched": False}
        with patch.object(fa, "_fetch_source", return_value="framework code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value=empty_res), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config") as mock_derive:
            events = _events(fa.deep_analyze_stream("PIPELINE", "p1", "c.s.t"))
        # the empty table is reported as a warn, not an "ok"
        qc = _steps(events, "query_config")
        assert any(e["status"] == "warn" and "empty" in e["message"] for e in qc)
        r = _result(events)
        assert r["derived"] is False
        assert r["reason_code"] == "config_empty"
        assert r["config_tables"] == ["c.s.cfg"]
        assert r["empty_config_tables"] == ["c.s.cfg"]
        assert "empty" in r["detail"]
        assert "pipeline" in r["detail"]  # friendly noun, not "PIPELINE"/"pipeline_"
        mock_derive.assert_not_called()  # short-circuited before the derive LLM call

    def test_empty_config_table_reported_once_not_thrice(self):
        # The same "empty" fact used to be emitted as a query_config warn, a derive
        # error AND the result detail. Only the per-table warn + the result remain.
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": True}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.cfg", "columns": [], "rows": [], "total_rows": 0, "matched": False}
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value=empty_res), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config"):
            events = _events(fa.deep_analyze_stream("PIPELINE", "p1", "c.s.t"))
        assert _steps(events, "derive") == []            # no redundant derive event
        assert len([e for e in events if e.get("type") == "step"
                    and "empty" in e.get("message", "")]) == 1

    def test_uncertain_empty_table_falls_through_to_derive(self):
        # `certain: false` means the table NAME was only guessed from a variable, so
        # its emptiness proves nothing — derivation must still be attempted.
        cfg = {"config_tables": [{"name": "c.s.guess", "certain": False}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.guess", "columns": [], "rows": [], "total_rows": 0, "matched": False}
        good_cols = [{"target_column": "x", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value=empty_res), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols) as mock_derive, \
             patch.object(fa.analysis_store, "save_analysis", return_value=3):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        mock_derive.assert_called_once()                 # NOT short-circuited
        r = _result(events)
        assert r["derived"] is True and r.get("reason_code") is None

    def test_empty_table_with_parameters_still_derives(self):
        # The framework passes its column map as a pipeline parameter; the config
        # table is a per-run audit table that happens to be empty. Deriving from
        # source + parameters must still run (this path regressed once).
        cfg = {"config_tables": [{"name": "c.s.audit", "certain": True}],
               "parameters": ["column_config"], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.audit", "columns": [], "rows": [], "total_rows": 0, "matched": False}
        good_cols = [{"target_column": "y", "source_columns": ["b"],
                      "expression": "b*2", "category": "ARITHMETIC"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={"column_config": '{"y": "b*2"}'}), \
             patch.object(fa, "_query_config_table", return_value=empty_res), \
             patch.object(fa, "_fetch_target_columns", return_value=["y"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols) as mock_derive, \
             patch.object(fa.analysis_store, "save_analysis", return_value=4):
            events = _events(fa.deep_analyze_stream("PIPELINE", "p1", "c.s.t"))
        mock_derive.assert_called_once()
        assert mock_derive.call_args.kwargs["parameters"] == {"column_config": '{"y": "b*2"}'}
        assert _result(events)["derived"] is True

    def test_unreadable_plus_empty_reports_grant_not_rerun(self):
        # One table can't be SELECTed, another is empty. Claiming "the tables are
        # empty, re-run the pipeline" would send the user after the wrong fix, so
        # the permission blocker must appear in the final detail.
        cfg = {"config_tables": [{"name": "c.s.denied", "certain": True},
                                 {"name": "c.s.empty", "certain": True}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.empty", "columns": [], "rows": [], "total_rows": 0, "matched": False}

        def _q(name, *_a, **_k):
            if name == "c.s.denied":
                raise RuntimeError("PERMISSION_DENIED")
            return empty_res

        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", side_effect=_q), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=[]):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        r = _result(events)
        assert r["derived"] is False
        assert r["reason_code"] == "no_columns"          # not the misleading config_empty
        assert "c.s.denied" in r["detail"] and "SELECT" in r["detail"]
        assert r["unreadable_config_tables"] == ["c.s.denied"]
        assert r["empty_config_tables"] == ["c.s.empty"]

    def test_duplicate_config_table_names_are_deduped(self):
        # Free-form LLM JSON can repeat a table; it must be queried and named once.
        cfg = {"config_tables": [{"name": "c.s.cfg"}, {"name": "c.s.cfg", "certain": False}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        empty_res = {"table": "c.s.cfg", "columns": [], "rows": [], "total_rows": 0, "matched": False}
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value=empty_res) as mock_q, \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=[]):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert mock_q.call_count == 1
        assert _result(events)["empty_config_tables"] == ["c.s.cfg"]

    def test_result_missing_total_rows_is_treated_as_empty(self):
        # A partial dict from _query_config_table must not raise KeyError mid-stream.
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": True}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_query_config_table", return_value={"table": "c.s.cfg"}), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config"):
            events = _events(fa.deep_analyze_stream("PIPELINE", "p1", "c.s.t"))
        assert _result(events)["reason_code"] == "config_empty"

    def test_reason_codes_on_early_failures(self):
        with patch.object(fa, "_fetch_source", return_value=" "):
            r = _result(_events(fa.deep_analyze_stream("JOB", "1", "c.s.t")))
        assert r["reason_code"] == "no_source"
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value={"error": "boom"}):
            r = _result(_events(fa.deep_analyze_stream("JOB", "1", "c.s.t")))
        assert r["reason_code"] == "detect_failed"

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


# ---------------------------------------------------------------------------
# config-table schema resolution (the LLM guessed the target's schema)
# ---------------------------------------------------------------------------

class TestConfigTableResolution:
    def test_is_table_not_found_matches_uc_error(self):
        assert fa._is_table_not_found(RuntimeError("SQL failed: [TABLE_OR_VIEW_NOT_FOUND] `c`.`s`.`t`"))
        assert fa._is_table_not_found(RuntimeError("[SCHEMA_NOT_FOUND] ..."))
        assert not fa._is_table_not_found(RuntimeError("no perms"))
        assert not fa._is_table_not_found(None)

    def test_resolve_alternates_finds_sibling_schema(self):
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql",
                   return_value=[{"table_schema": "mapping_factory"}]):
            alts = fa._resolve_config_alternates(
                "cat.mapping_factory_fin.mf_pipeline_config",
                "cat.mapping_factory_fin.fact_account_balance")
        assert alts == ["cat.mapping_factory.mf_pipeline_config"]

    def test_resolve_alternates_bad_target_shape_returns_empty(self):
        assert fa._resolve_config_alternates("c.s.cfg", "not_a_fqn") == []

    def test_resolve_alternates_swallows_query_failure(self):
        with patch("backend.lineage_service._get_client", return_value=MagicMock()), \
             patch("backend.lineage_service._execute_sql", side_effect=RuntimeError("no warehouse")):
            assert fa._resolve_config_alternates("c.s.cfg", "c.s.t") == []

    def test_deep_analyze_resolves_wrong_schema_and_succeeds(self):
        # The LLM qualifies the config table with the target's schema (which
        # doesn't exist); resolution finds the real sibling schema and retries.
        cfg = {"config_tables": [{"name": "c.mapping_factory_fin.mf_pipeline_config", "certain": False}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        good_cols = [{"target_column": "bal", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        good_res = {"table": "x", "columns": ["target_name"],
                    "rows": [{"target_name": "t"}], "total_rows": 3, "matched": True}

        def _qc(fqn, target, keys):
            if fqn == "c.mapping_factory_fin.mf_pipeline_config":
                raise RuntimeError("SQL failed: [TABLE_OR_VIEW_NOT_FOUND] wrong schema")
            return good_res

        with patch.object(fa, "_fetch_source", return_value="framework code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_resolve_config_alternates",
                          return_value=["c.mapping_factory.mf_pipeline_config"]), \
             patch.object(fa, "_query_config_table", side_effect=_qc), \
             patch.object(fa, "_fetch_target_columns", return_value=["bal"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", return_value=7):
            events = _events(fa.deep_analyze_stream(
                "PIPELINE", "p1", "c.mapping_factory_fin.fact_account_balance"))
        qc = _steps(events, "query_config")
        assert any(e["status"] == "ok" and "Resolved" in e["message"] for e in qc)
        assert qc[-1]["status"] == "ok"
        r = _result(events)
        assert r["derived"] is True
        assert r["columns"] == good_cols

    def test_deep_analyze_not_found_with_no_alternates_skips(self):
        cfg = {"config_tables": [{"name": "c.s.cfg", "certain": False}],
               "parameters": [], "target_key_columns": [], "notes": ""}
        good_cols = [{"target_column": "x", "source_columns": ["a"],
                      "expression": "a", "category": "PASS_THROUGH"}]
        with patch.object(fa, "_fetch_source", return_value="code"), \
             patch.object(fa.llm_client, "detect_framework_config", return_value=cfg), \
             patch.object(fa, "_fetch_entity_parameters", return_value={}), \
             patch.object(fa, "_resolve_config_alternates", return_value=[]), \
             patch.object(fa, "_query_config_table",
                          side_effect=RuntimeError("SQL failed: [TABLE_OR_VIEW_NOT_FOUND] x")), \
             patch.object(fa, "_fetch_target_columns", return_value=["x"]), \
             patch.object(fa.llm_client, "derive_columns_from_config", return_value=good_cols), \
             patch.object(fa.analysis_store, "save_analysis", return_value=3):
            events = _events(fa.deep_analyze_stream("JOB", "1", "c.s.t"))
        assert _steps(events, "query_config")[-1]["status"] == "warn"
        assert _result(events)["derived"] is True
