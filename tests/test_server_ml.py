"""Unit tests for backend.server.ml (the SERVICE module, not the routes).

Covers:
- _execute_sql (no-warehouse, success, non-success, empty)
- _ensure_model_lineage_table (success + swallowed error)
- list_serving_endpoints / get_endpoint_usage (happy + error)
- _run_input_tables (empty run_id, API error, source parse, bad source)
- _endpoints_for_model (happy + error)
- _derive_models_for_table_live (models list error, version error, match)
- get_models_for_table (live hit, live empty->fallback, fallback error)
- get_table_for_model (with/without version, error)
- register_model_lineage (insert + return shape)
"""
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState

import backend.server.ml as ml


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
        with patch.object(ml, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                ml._execute_sql("SELECT 1")

    def test_success(self):
        client = _make_client(columns=["x"], data=[["v"]])
        with patch.object(ml, "WAREHOUSE_ID", "wh"), patch.object(ml, "_get_client", return_value=client):
            assert ml._execute_sql("SELECT x") == [{"x": "v"}]

    def test_failed_state(self):
        client = _make_client(state=StatementState.FAILED, error_msg="err")
        with patch.object(ml, "WAREHOUSE_ID", "wh"), patch.object(ml, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="err"):
                ml._execute_sql("SELECT 1")

    def test_empty(self):
        client = _make_client(data=None)
        with patch.object(ml, "WAREHOUSE_ID", "wh"), patch.object(ml, "_get_client", return_value=client):
            assert ml._execute_sql("SELECT 1") == []


class TestEnsureTable:
    def test_success(self):
        with patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml._ensure_model_lineage_table()
        assert "CREATE TABLE IF NOT EXISTS" in m.call_args[0][0]

    def test_error_swallowed(self):
        with patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            ml._ensure_model_lineage_table()  # no raise


class TestListServingEndpoints:
    def test_happy(self):
        rows = [{"endpoint_name": "e1"}]
        with patch.object(ml, "_execute_sql", return_value=rows):
            assert ml.list_serving_endpoints() == rows

    def test_error(self):
        with patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            assert ml.list_serving_endpoints() == []


class TestEndpointUsage:
    def test_happy_and_escaping(self):
        with patch.object(ml, "_execute_sql", return_value=[{"day": "d"}]) as m:
            out = ml.get_endpoint_usage("ep'; DROP")
        assert out == [{"day": "d"}]
        # The quote is DOUBLED (escaped), not stripped: the payload stays inside
        # the literal instead of terminating it.
        assert "endpoint_name = 'ep''; DROP'" in m.call_args[0][0]

    def test_backslash_escaped_before_quote(self):
        """`\\'` must become `\\\\''` — escaping the quote alone would let the
        backslash consume it and re-open the literal."""
        with patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.get_endpoint_usage("ep\\' OR 1=1 -- ")
        assert "endpoint_name = 'ep\\\\'' OR 1=1 -- '" in m.call_args[0][0]

    def test_error(self):
        with patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            assert ml.get_endpoint_usage("ep") == []


class TestRunInputTables:
    def test_empty_run_id(self):
        assert ml._run_input_tables(MagicMock(), "") == []

    def test_api_error(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("x")
        assert ml._run_input_tables(client, "r1") == []

    def test_parses_table_from_string_source(self):
        client = MagicMock()
        client.api_client.do.return_value = {
            "run": {"inputs": {"dataset_inputs": [
                {"dataset": {"source": '{"table_name": "c.s.t"}'}},
            ]}}
        }
        assert ml._run_input_tables(client, "r1") == ["c.s.t"]

    def test_dict_source_and_missing_source_skipped(self):
        client = MagicMock()
        client.api_client.do.return_value = {
            "run": {"inputs": {"dataset_inputs": [
                {"dataset": {"source": {"table_name": "c.s.t2"}}},
                {"dataset": {}},  # no source -> skipped
                {"dataset": {"source": "not-json"}},  # parse error -> skipped
            ]}}
        }
        assert ml._run_input_tables(client, "r1") == ["c.s.t2"]


class TestEndpointsForModel:
    def test_happy(self):
        rows = [{"endpoint_name": "e1"}, {"endpoint_name": None}]
        with patch.object(ml, "_execute_sql", return_value=rows):
            assert ml._endpoints_for_model(MagicMock(), "m'x") == ["e1"]

    def test_error(self):
        with patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            assert ml._endpoints_for_model(MagicMock(), "m") == []


class TestDeriveModelsLive:
    def test_models_list_error(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("no perms")
        with patch.object(ml, "_get_client", return_value=client):
            assert ml._derive_models_for_table_live("c", "s", "t") == []

    def test_match_found(self):
        client = MagicMock()

        def fake_do(method, path, query=None, body=None):
            if path == "/api/2.1/unity-catalog/models":
                return {"registered_models": [{"full_name": "c.s.m"}, {}]}  # 2nd has no full_name
            if path.endswith("/versions"):
                return {"model_versions": [{"version": 1, "run_id": "r1", "created_by": "u"}]}
            return {}

        client.api_client.do.side_effect = fake_do
        with patch.object(ml, "_get_client", return_value=client), \
             patch.object(ml, "_run_input_tables", return_value=["c.s.t"]), \
             patch.object(ml, "_endpoints_for_model", return_value=["ep"]):
            out = ml._derive_models_for_table_live("c", "s", "t")
        assert out[0]["model_name"] == "c.s.m"
        assert out[0]["endpoints"] == ["ep"]

    def test_version_fetch_error_skips_model(self):
        client = MagicMock()

        def fake_do(method, path, query=None, body=None):
            if path == "/api/2.1/unity-catalog/models":
                return {"registered_models": [{"full_name": "c.s.m"}]}
            raise RuntimeError("version fail")

        client.api_client.do.side_effect = fake_do
        with patch.object(ml, "_get_client", return_value=client):
            assert ml._derive_models_for_table_live("c", "s", "t") == []

    def test_no_match(self):
        client = MagicMock()

        def fake_do(method, path, query=None, body=None):
            if path == "/api/2.1/unity-catalog/models":
                return {"registered_models": [{"full_name": "c.s.m"}]}
            return {"model_versions": [{"version": 1, "run_id": "r1"}]}

        client.api_client.do.side_effect = fake_do
        with patch.object(ml, "_get_client", return_value=client), \
             patch.object(ml, "_run_input_tables", return_value=["other.t"]):
            assert ml._derive_models_for_table_live("c", "s", "t") == []


class TestGetModelsForTable:
    def test_live_hit(self):
        live = [{"model_name": "m"}]
        with patch.object(ml, "_derive_models_for_table_live", return_value=live):
            assert ml.get_models_for_table("c", "s", "t") == live

    def test_live_empty_falls_back_to_table(self):
        with patch.object(ml, "_derive_models_for_table_live", return_value=[]), \
             patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[{"model_name": "reg"}]):
            assert ml.get_models_for_table("c", "s", "t") == [{"model_name": "reg"}]

    def test_live_error_then_fallback(self):
        with patch.object(ml, "_derive_models_for_table_live", side_effect=RuntimeError("x")), \
             patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]):
            assert ml.get_models_for_table("c", "s", "t") == []

    def test_fallback_error(self):
        with patch.object(ml, "_derive_models_for_table_live", return_value=[]), \
             patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            assert ml.get_models_for_table("c", "s", "t") == []


class TestGetTableForModel:
    def test_with_version(self):
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[{"training_table": "c.s.t"}]) as m:
            out = ml.get_table_for_model("m'x", "3")
        assert out == [{"training_table": "c.s.t"}]
        assert "model_version = '3'" in m.call_args[0][0]
        # model_name is escaped, not quote-stripped
        assert "model_name = 'm''x'" in m.call_args[0][0]

    def test_version_union_payload_stays_inside_literal(self):
        """The confirmed exploit: model_version reached the WHERE clause with no
        treatment at all. The route now 400s it, and the sink escapes it, so even
        a direct service-layer caller cannot break out."""
        payload = (
            "y' AND 1=0 UNION SELECT CAST(ssn AS STRING) "
            "FROM main.finance.payroll -- "
        )
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.get_table_for_model("x", payload)
        sql = m.call_args[0][0]
        # Every quote in the payload is doubled, so the UNION is inert text.
        assert "model_version = 'y'' AND 1=0 UNION SELECT" in sql
        assert "model_version = 'y' AND" not in sql

    def test_version_backslash_quote_escaped(self):
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.get_table_for_model("x", "3\\' OR 1=1 -- ")
        assert "model_version = '3\\\\'' OR 1=1 -- '" in m.call_args[0][0]

    def test_without_version(self):
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.get_table_for_model("m")
        assert "model_version =" not in m.call_args[0][0]

    def test_error(self):
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", side_effect=RuntimeError("x")):
            assert ml.get_table_for_model("m") == []


class TestRegisterModelLineage:
    def test_inserts_and_returns_status(self):
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            out = ml.register_model_lineage("mdl", "1", "c.s.t", job_id="j", notes="n'x")
        assert out == {"status": "registered", "model_name": "mdl", "training_table": "c.s.t"}
        assert "INSERT INTO" in m.call_args[0][0]

    def test_short_table_name(self):
        # training_table with no dots -> tt falls back to whole string, tc/ts empty
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.register_model_lineage("mdl", "1", "flat")
        assert "'flat'" in m.call_args[0][0]

    def test_trailing_backslash_cannot_shift_quoting_parity(self):
        """Every field here is raw request-body input with no allow-list. Under the
        old quote-STRIPPING, a value ending in a backslash escaped its literal's
        closing quote, so the NEXT field's content was parsed as SQL. Doubling the
        backslash keeps each field inside its own literal."""
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.register_model_lineage(
                "mdl\\", "1", "c.s.t",
                notes=", (SELECT current_user()), 'x",
            )
        sql = m.call_args[0][0]
        assert "'mdl\\\\'" in sql            # backslash doubled -> literal closes
        assert "'mdl\\'," not in sql         # ...never left as a lone escape
        assert "''x'" in sql                 # the notes quote is doubled too

    def test_none_fields_become_empty_literals(self):
        """sql_str(None) is '' — the old `(s or "")` lambda behaviour is preserved."""
        with patch.object(ml, "_ensure_model_lineage_table"), \
             patch.object(ml, "_execute_sql", return_value=[]) as m:
            ml.register_model_lineage("mdl", "1", "c.s.t", job_id=None, notes=None)
        assert "'mdl', '1', 'c.s.t'" in m.call_args[0][0]
        assert "None" not in m.call_args[0][0]
