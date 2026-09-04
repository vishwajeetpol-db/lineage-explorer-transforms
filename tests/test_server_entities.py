"""Tests for backend.server.entities (capability 25 — producer entity resolution).

Covers _workspace_url, each per-type resolver (happy path + SDK-error fallback),
the resolve_entity dispatch table, batch resolve, and the LRU-cache clearing.
An autouse fixture clears the entity cache before every test to avoid bleed.
"""
from unittest.mock import MagicMock, patch

import pytest

from backend.server import entities


@pytest.fixture(autouse=True)
def _clear_cache():
    entities.clear_entity_cache()
    yield
    entities.clear_entity_cache()


def _client_with_host(host="https://myworkspace.databricks.com"):
    client = MagicMock()
    client.config.host = host
    return client


class TestWorkspaceUrl:
    def test_strips_trailing_slash(self):
        client = _client_with_host("https://ws.databricks.com/")
        with patch.object(entities, "_get_client", return_value=client):
            assert entities._workspace_url() == "https://ws.databricks.com"

    def test_exception_returns_empty(self):
        with patch.object(entities, "_get_client", side_effect=RuntimeError):
            assert entities._workspace_url() == ""


class TestResolveJob:
    def test_happy(self):
        client = _client_with_host()
        job = MagicMock()
        job.settings.name = "Nightly ETL"
        job.creator_user_name = "alice"
        client.jobs.get.return_value = job
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("JOB", "123")
        assert out["display_name"] == "Nightly ETL"
        assert out["deep_link"] == "https://myworkspace.databricks.com/#job/123"
        assert out["creator"] == "alice"

    def test_no_settings(self):
        client = _client_with_host()
        job = MagicMock()
        job.settings = None
        client.jobs.get.return_value = job
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("JOB", "77")
        assert out["display_name"] == "Job 77"

    def test_sdk_error_fallback(self):
        client = _client_with_host()
        client.jobs.get.side_effect = RuntimeError("nope")
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("JOB", "9")
        assert out["display_name"] == "Job 9"
        assert out["deep_link"] is None


class TestResolvePipeline:
    def test_happy(self):
        client = _client_with_host()
        p = MagicMock()
        p.name = "My Pipeline"
        p.creator_user_name = "bob"
        client.pipelines.get.return_value = p
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("PIPELINE", "abcdef123456")
        assert out["display_name"] == "My Pipeline"
        assert "pipelines/abcdef123456" in out["deep_link"]

    def test_empty_name_falls_back(self):
        client = _client_with_host()
        p = MagicMock()
        p.name = ""
        client.pipelines.get.return_value = p
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("PIPELINE", "abcdef123456")
        assert out["display_name"] == "Pipeline abcdef12"

    def test_error_fallback(self):
        client = _client_with_host()
        client.pipelines.get.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("PIPELINE", "abcdef123456")
        assert out["display_name"] == "Pipeline abcdef12"


class TestResolveNotebook:
    def test_path_based(self):
        client = _client_with_host()
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("NOTEBOOK", "/Users/x/nb")
        assert out["display_name"] == "nb"
        assert out["deep_link"].endswith("/#workspace/Users/x/nb")

    def test_numeric_id(self):
        client = _client_with_host()
        obj = MagicMock()
        obj.path = "/Repos/proj/etl_nb"
        client.workspace.get_status.return_value = obj
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("NOTEBOOK", "42")
        assert out["display_name"] == "etl_nb"
        assert out["deep_link"].endswith("/#notebook/42")

    def test_error_fallback(self):
        client = _client_with_host()
        client.workspace.get_status.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("NOTEBOOK", "42")
        assert out["display_name"] == "42"


class TestResolveQuery:
    def test_happy(self):
        client = _client_with_host()
        q = MagicMock()
        q.name = "Sales Query"
        q.user = {"name": "carol"}
        client.queries.get.return_value = q
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("QUERY", "qid12345678")
        assert out["display_name"] == "Sales Query"
        assert out["deep_link"].endswith("/sql/queries/qid12345678")

    def test_dbsql_query_alias(self):
        client = _client_with_host()
        q = MagicMock()
        q.name = None
        del q.user  # no user attr -> owner None branch
        client.queries.get.return_value = q
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("DBSQL_QUERY", "qid12345678")
        assert out["display_name"] == "Query qid12345"
        assert out["owner"] is None

    def test_error_fallback(self):
        client = _client_with_host()
        client.queries.get.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("QUERY", "qid12345678")
        assert out["display_name"] == "Query qid12345"


class TestResolveDashboard:
    def test_legacy_dashboard(self):
        client = _client_with_host()
        d = MagicMock()
        d.name = "Legacy Dash"
        client.dashboards.get.return_value = d
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("DBSQL_DASHBOARD", "dash12345678")
        assert out["display_name"] == "Legacy Dash"
        assert out["deep_link"].endswith("/sql/dashboards/dash12345678")

    def test_legacy_dashboard_error(self):
        client = _client_with_host()
        client.dashboards.get.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("DASHBOARD", "dash12345678")
        assert out["display_name"] == "Dashboard dash1234"

    def test_lakeview_v3_happy(self):
        client = _client_with_host()
        d = MagicMock()
        d.display_name = "AI/BI Dash"
        client.lakeview.get.return_value = d
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("DASHBOARD_V3", "dv312345678")
        assert out["display_name"] == "AI/BI Dash"
        assert out["deep_link"].endswith("/dashboardsv3/dv312345678")

    def test_lakeview_v3_error_still_has_link(self):
        client = _client_with_host()
        client.lakeview.get.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("DASHBOARD_V3", "dv312345678")
        assert out["display_name"] == "Dashboard dv312345"
        assert out["deep_link"].endswith("/dashboardsv3/dv312345678")


class TestDispatchAndBatch:
    def test_unknown_type_default(self):
        out = entities.resolve_entity("FOOBAR", "xyz")
        assert out["display_name"] == "FOOBAR xyz"
        assert out["deep_link"] is None

    def test_case_insensitive(self):
        client = _client_with_host()
        client.jobs.get.side_effect = RuntimeError
        with patch.object(entities, "_get_client", return_value=client):
            out = entities.resolve_entity("job", "5")
        assert out["entity_type"] == "JOB"

    def test_resolve_entities_batch(self):
        with patch.object(entities, "_get_client", return_value=_client_with_host()):
            out = entities.resolve_entities([
                {"entity_type": "FOO", "entity_id": "1"},
                {},  # missing keys -> empty strings
            ])
        assert len(out) == 2
        assert out[0]["display_name"] == "FOO 1"

    def test_clear_cache_runs(self):
        entities.clear_entity_cache()  # must not raise
