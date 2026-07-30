"""Unit tests for backend.server.llm.

The LLM call goes through client.api_client.do(...) against a serving endpoint.
Covers:
- empty-source short-circuit
- happy JSON-array parse (default endpoint)
- markdown-fence stripping
- {"columns": [...]} dict envelope
- LLM_ENDPOINT_URL override path
- target_columns hint branch
- exception -> [] path
- is_llm_configured true/false
"""
from unittest.mock import patch, MagicMock

import pytest

import backend.server.llm as llm


def _client_returning(content):
    client = MagicMock()
    client.api_client.do.return_value = {"choices": [{"message": {"content": content}}]}
    return client


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

    def test_endpoint_url_override(self):
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": "https://x/invocations"}):
            llm.analyze_source_code("SELECT 1", "c.s.t")
        args = client.api_client.do.call_args
        assert args[0][1] == "https://x/invocations"

    def test_target_columns_hint_and_model_override(self):
        client = _client_returning('[]')
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            llm.analyze_source_code("SELECT 1", "c.s.t", target_columns=["a", "b"], model="mymodel")
        body = client.api_client.do.call_args.kwargs["body"]
        user_msg = body["messages"][1]["content"]
        assert "output columns" in user_msg
        assert "/serving-endpoints/mymodel/invocations" == client.api_client.do.call_args[0][1]

    def test_exception_returns_empty(self):
        client = MagicMock()
        client.api_client.do.side_effect = RuntimeError("endpoint down")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            assert llm.analyze_source_code("SELECT 1", "c.s.t") == []

    def test_malformed_json_returns_empty(self):
        client = _client_returning("this is not json")
        with patch("backend.lineage_service._get_client", return_value=client), \
             patch.object(llm.os, "environ", {**llm.os.environ, "LLM_ENDPOINT_URL": ""}):
            assert llm.analyze_source_code("SELECT 1", "c.s.t") == []


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
