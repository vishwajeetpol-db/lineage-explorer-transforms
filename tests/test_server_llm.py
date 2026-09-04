"""Unit tests for backend.server.llm.

The LLM call goes through client.api_client.do(...) against a serving endpoint.
Covers:
- empty-source short-circuit
- happy JSON-array parse (default endpoint)
- markdown-fence stripping
- {"columns": [...]} dict envelope
- LLM_ENDPOINT_URL override: honoured only when it stays in this workspace
- per-user call budget
- target_columns hint branch
- exception -> [] path
- is_llm_configured true/false
"""
from unittest.mock import patch, MagicMock

import pytest

import backend.server.llm as llm


def _client_returning(content, host="https://myworkspace.cloud.databricks.com"):
    client = MagicMock()
    client.api_client.do.return_value = {"choices": [{"message": {"content": content}}]}
    client.config.host = host
    return client


@pytest.fixture(autouse=True)
def _reset_llm_budget():
    """The per-user call budget is module-global; keep it out of sibling tests."""
    llm._reset_llm_budget()
    yield
    llm._reset_llm_budget()


class TestAnalyzeSourceCode:
    def test_empty_source_short_circuits(self):
        assert llm.analyze_source_code("", "c.s.t") == []
        assert llm.analyze_source_code("   ", "c.s.t") == []

    def test_happy_json_array(self):
        client = _client_returning('[{"target_column": "x", "source_columns": ["a"]}]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.dict("os.environ", {}, clear=False), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.analyze_source_code("SELECT a AS x", "c.s.t")
        assert out == [{"target_column": "x", "source_columns": ["a"]}]
        # default endpoint path used
        args = client.api_client.do.call_args
        assert "/serving-endpoints/" in args[0][1]

    def test_markdown_fence_stripped(self):
        client = _client_returning('```json\n[{"target_column": "x"}]\n```')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.analyze_source_code("SELECT 1", "c.s.t")
        assert out == [{"target_column": "x"}]

    def test_dict_columns_envelope(self):
        client = _client_returning('{"columns": [{"target_column": "y"}]}')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.analyze_source_code("SELECT 1", "c.s.t")
        assert out == [{"target_column": "y"}]

    def test_off_workspace_override_is_refused(self):
        """An override pointing off-workspace must NOT be used.

        This call ships producer source code. The override was previously honoured
        verbatim and the SDK attaches the workspace credential to whatever it is
        given, so one wrong env var shipped regulated source text to a third party
        with a valid token. A config mistake must not become an egress — so the bad
        value is refused and the in-workspace endpoint is used instead.
        """
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ",
                          {**llm.os.environ, "LLM_ENDPOINT_URL": "https://evil.example.com/invocations"}):
            llm.analyze_source_code("SELECT 1", "c.s.t")
        url = client.api_client.do.call_args[0][1]
        assert "evil.example.com" not in url
        assert url == f"/serving-endpoints/{llm.LLM_MODEL_NAME}/invocations"

    def test_same_host_absolute_override_is_honoured(self):
        """A legitimate absolute URL on the workspace's own host still works."""
        host = "https://myworkspace.cloud.databricks.com"
        client = _client_returning('[]', host=host)
        override = f"{host}/serving-endpoints/custom/invocations"
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": override}):
            llm.analyze_source_code("SELECT 1", "c.s.t")
        assert client.api_client.do.call_args[0][1] == override

    def test_relative_serving_path_override_is_honoured(self):
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ",
                          {**llm.os.environ, "LLM_ENDPOINT_URL": "/serving-endpoints/custom/invocations"}):
            llm.analyze_source_code("SELECT 1", "c.s.t")
        assert client.api_client.do.call_args[0][1] == "/serving-endpoints/custom/invocations"

    def test_non_serving_relative_override_is_refused(self):
        """A relative path outside the serving namespace is still a redirect."""
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ",
                          {**llm.os.environ, "LLM_ENDPOINT_URL": "/api/2.0/workspace/export"}):
            llm.analyze_source_code("SELECT 1", "c.s.t")
        assert client.api_client.do.call_args[0][1] == f"/serving-endpoints/{llm.LLM_MODEL_NAME}/invocations"


class TestLLMBudget:
    """The deep-analysis path is agentic: one user action can be many model calls."""

    def test_budget_exhaustion_raises(self):
        with patch.object(llm, "LLM_MAX_CALLS_PER_USER_PER_DAY", 3):
            for _ in range(3):
                llm._charge_llm_budget()
            with pytest.raises(llm.LLMBudgetError, match="daily limit"):
                llm._charge_llm_budget()

    def test_budget_is_per_user(self):
        from backend import warehouse_gate as wg
        with patch.object(llm, "LLM_MAX_CALLS_PER_USER_PER_DAY", 2):
            wg.set_request_context("/api/x", "alice@example.com")
            llm._charge_llm_budget()
            llm._charge_llm_budget()
            with pytest.raises(llm.LLMBudgetError):
                llm._charge_llm_budget()
            # A different person is unaffected by Alice's spend.
            wg.set_request_context("/api/x", "bob@example.com")
            llm._charge_llm_budget()

    def test_budget_resets_on_a_new_day(self):
        with patch.object(llm, "LLM_MAX_CALLS_PER_USER_PER_DAY", 1):
            llm._charge_llm_budget()
            with pytest.raises(llm.LLMBudgetError):
                llm._charge_llm_budget()
            llm._llm_budget_day = "1999-01-01"   # simulate the day rolling over
            llm._charge_llm_budget()

    def test_status_reports_usage(self):
        llm._charge_llm_budget()
        st = llm.get_llm_budget()
        assert st["calls_today"] == 1
        assert st["max_per_user_per_day"] == llm.LLM_MAX_CALLS_PER_USER_PER_DAY

    def test_target_columns_hint_and_model_override(self):
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            llm.analyze_source_code("SELECT 1", "c.s.t", target_columns=["a", "b"], model="mymodel")
        body = client.api_client.do.call_args.kwargs["body"]
        user_msg = body["messages"][1]["content"]
        assert "output columns" in user_msg
        assert "/serving-endpoints/mymodel/invocations" == client.api_client.do.call_args[0][1]

    def test_exception_propagates(self):
        # A REAL invocation failure (endpoint down / bad request / timeout) now
        # propagates so the caller can surface a specific error, instead of being
        # swallowed to [] (which was indistinguishable from "no columns found").
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("endpoint down")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            with pytest.raises(RuntimeError, match="endpoint down"):
                llm.analyze_source_code("SELECT 1", "c.s.t")

    def test_malformed_json_returns_empty(self):
        client = _client_returning("this is not json")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            assert llm.analyze_source_code("SELECT 1", "c.s.t") == []


class TestInvokeChat:
    def test_content_block_list_is_flattened(self):
        # Anthropic/Claude-style endpoints return content as a list of blocks.
        blocks = [{"type": "text", "text": "[]"}, {"type": "text", "text": ""}, "tail"]
        client = MagicMock()
        client.api_client.do.return_value = {"choices": [{"message": {"content": blocks}}]}
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm._invoke_chat([{"role": "user", "content": "hi"}])
        assert out == "[]tail"

    def test_temperature_retry_without_param(self):
        # First call rejects `temperature`; retry (without it) succeeds.
        client = MagicMock()
        client.api_client.do.side_effect = [
            RuntimeError("BAD_REQUEST: temperature is not supported"),
            {"choices": [{"message": {"content": "ok"}}]},
        ]
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm._invoke_chat([{"role": "user", "content": "hi"}], temperature=0.5)
        assert out == "ok"
        assert client.api_client.do.call_count == 2
        # The retry payload dropped temperature.
        retry_body = client.api_client.do.call_args_list[1].kwargs["body"]
        assert "temperature" not in retry_body

    def test_unrelated_error_reraises(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("gateway timeout")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            with pytest.raises(RuntimeError, match="gateway timeout"):
                llm._invoke_chat([{"role": "user", "content": "hi"}])


class TestExplainTransformations:
    def test_empty_columns_short_circuits(self):
        assert llm.explain_transformations([], "c.s.t") == {"summary": "", "columns": []}

    def test_happy_object(self):
        content = '{"summary": "joins A and B", "columns": [{"column": "x", "explanation": "a+b"}]}'
        client = _client_returning(content)
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_transformations([{"target_column": "x", "source_columns": ["a"]}], "c.s.t")
        assert out["summary"] == "joins A and B"
        assert out["columns"] == [{"column": "x", "explanation": "a+b"}]

    def test_array_response_wrapped(self):
        client = _client_returning('[{"column": "x", "explanation": "e"}]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_transformations([{"column": "x"}], "c.s.t")
        assert out["summary"] == ""
        assert out["columns"] == [{"column": "x", "explanation": "e"}]

    def test_error_returns_error_key(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("endpoint down")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_transformations([{"column": "x"}], "c.s.t")
        assert out["columns"] == [] and "endpoint down" in out["error"]


class TestExplainLineageGraph:
    def test_empty_nodes_short_circuits(self):
        assert llm.explain_lineage_graph([], [], "c.s.t") == {"summary": "", "steps": []}

    def test_happy_object_with_steps(self):
        content = ('{"summary": "Raw orders become curated orders.", '
                   '"steps": [{"title": "Ingest", "detail": "Raw orders land."}]}')
        client = _client_returning(content)
        nodes = [{"id": "n1", "label": "Raw Orders", "type": "Dataset"},
                 {"id": "n2", "label": "Curated Orders", "type": "Dataset"}]
        edges = [{"source": "n1", "target": "n2"}, {"source": "x", "target": "n2"}]
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_lineage_graph(nodes, edges, "c.s.curated", detail="data")
        assert out["summary"].startswith("Raw orders")
        assert out["steps"] == [{"title": "Ingest", "detail": "Raw orders land."}]
        # Edges referencing an unknown node id are dropped from the prompt.
        user_msg = client.api_client.do.call_args.kwargs["body"]["messages"][1]["content"]
        assert "Raw Orders -> Curated Orders" in user_msg
        assert "datasets only" in user_msg

    def test_non_dict_response_defaults_empty(self):
        client = _client_returning("[1, 2]")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_lineage_graph([{"id": "n1", "label": "A", "type": "Dataset"}], [], "c.s.t")
        assert out == {"summary": "", "steps": []}

    def test_error_returns_error_key(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("endpoint down")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.explain_lineage_graph([{"id": "n1", "label": "A", "type": "Dataset"}], [], "c.s.t")
        assert out["summary"] == "" and out["steps"] == [] and "endpoint down" in out["error"]


class TestDetectFrameworkConfig:
    def test_empty_source_short_circuits(self):
        out = llm.detect_framework_config("  ", "c.s.t")
        assert out == {"config_tables": [], "parameters": [], "target_key_columns": [], "notes": ""}

    def test_happy_parse(self):
        content = ('{"config_tables": [{"name": "c.s.cfg", "certain": true}], '
                   '"parameters": ["run_date"], "target_key_columns": ["tgt"], "notes": "n"}')
        client = _client_returning(content)
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.detect_framework_config("code", "c.s.t")
        assert out["config_tables"] == [{"name": "c.s.cfg", "certain": True}]
        assert out["parameters"] == ["run_date"]
        assert out["notes"] == "n"

    def test_non_dict_response_defaults(self):
        client = _client_returning('[1, 2, 3]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.detect_framework_config("code", "c.s.t")
        assert out["config_tables"] == [] and out["notes"] == ""

    def test_error_returns_error_key(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("boom")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.detect_framework_config("code", "c.s.t")
        assert "boom" in out["error"]


class TestDeriveColumnsFromConfig:
    def test_happy_array(self):
        client = _client_returning('[{"target_column": "x", "source_columns": ["a"]}]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.derive_columns_from_config(
                "code", "c.s.t", ["x"], {"p": 1}, [{"table": "c.s.cfg", "rows": []}])
        assert out == [{"target_column": "x", "source_columns": ["a"]}]

    def test_dict_envelope(self):
        client = _client_returning('{"columns": [{"target_column": "y"}]}')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            out = llm.derive_columns_from_config("code", "c.s.t", None, {}, [])
        assert out == [{"target_column": "y"}]

    def test_error_returns_empty(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("boom")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            assert llm.derive_columns_from_config("code", "c.s.t", ["x"], {}, []) == []


class TestIsLlmConfigured:
    def test_true_when_client_available(self):
        with patch("backend.lineage_service._get_client", return_value=MagicMock()):
            assert llm.is_llm_configured() is True

    def test_false_when_client_none(self):
        with patch("backend.lineage_service._get_client", return_value=None):
            assert llm.is_llm_configured() is False

    def test_false_on_exception(self):
        with patch("backend.lineage_service._get_client", side_effect=RuntimeError("x")):
            assert llm.is_llm_configured() is False
