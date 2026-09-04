"""Deep coverage for backend/routes/impact.py — BFS + consumers + sensitivity.

Drives the downstream BFS, consumer grouping, owner enrichment, and
sensitive-affected branches by mocking the module _execute_sql + governance.
(Does not edit the existing test_routes_impact.py.)
"""
from unittest.mock import patch

import backend.routes.impact as impact


class TestBfsDownstream:
    def test_walks_multiple_hops(self):
        # hop1 -> b; hop2 -> c; hop3 -> empty
        with patch.object(impact, "_execute_sql", side_effect=[
            [{"target_table_full_name": "main.s.b"}],
            [{"target_table_full_name": "main.s.c"}],
            [],
        ]):
            out = impact._bfs_downstream("main.s.a", max_hops=5)
        assert out == {"main.s.b": 1, "main.s.c": 2}

    def test_error_breaks_gracefully(self):
        with patch.object(impact, "_execute_sql", side_effect=RuntimeError("timeout")):
            assert impact._bfs_downstream("main.s.a") == {}

    def test_skips_start_and_visited(self):
        with patch.object(impact, "_execute_sql", side_effect=[
            [{"target_table_full_name": "main.s.a"},  # start, skipped
             {"target_table_full_name": "main.s.b"}],
            [],
        ]):
            out = impact._bfs_downstream("main.s.a")
        assert "main.s.a" not in out and "main.s.b" in out


class TestConsumers:
    def test_groups_by_type_and_resolves(self):
        rows = [{"entity_type": "JOB", "entity_id": "1"},
                {"entity_type": "DASHBOARD_V3", "entity_id": "d1"}]
        with patch.object(impact, "_execute_sql", return_value=rows), \
             patch.object(impact, "resolve_entities",
                          side_effect=lambda e: [{**x, "display_name": "n"} for x in e]):
            out = impact._consumers(["main.s.a"])
        assert out["total"] == 2
        assert set(out["by_type"]) == {"JOB", "DASHBOARD_V3"}

    def test_empty_scope(self):
        with patch.object(impact, "_execute_sql", return_value=[]):
            out = impact._consumers([])
        assert out["total"] == 0


class TestImpactRouteFull:
    def test_full_enrichment(self, app_client):
        # _bfs_downstream -> one downstream; owner query; governance sensitivity;
        # consumers query. Patch the pieces the route composes.
        with patch.object(impact, "_bfs_downstream", return_value={"main.s.b": 1}), \
             patch.object(impact, "_execute_sql", return_value=[
                 {"full_name": "main.s.b", "table_owner": "o@x.com"}]), \
             patch.object(impact, "get_table_governance",
                          return_value={"sensitive_columns": [{"column": "ssn"}]}), \
             patch.object(impact, "_consumers", return_value={"by_type": {}, "total": 0, "entities": []}):
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "s", "table": "a"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["downstream_count"] == 1
        assert "main.s.b" in data["sensitive_affected"]
        assert "o@x.com" in data["consumer_owners"]
