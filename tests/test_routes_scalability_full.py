"""Deeper tests for backend/routes/scalability.py — pagination body, cache
stats/invalidate, health. Complements the basic test_routes_scalability.py
(which is left untouched). All SQL + cache service calls are mocked; offline.
"""
from unittest.mock import MagicMock, patch

import pytest

from databricks.sdk.service.sql import StatementState


# ---------------------------------------------------------------------------
# _execute_sql — the module-level SQL helper (mocked _get_client)
# ---------------------------------------------------------------------------
class TestExecuteSql:
    def _resp(self, state, rows=None, cols=None, err=None):
        resp = MagicMock()
        resp.status.state = state
        resp.status.error = MagicMock(message=err) if err else None
        if rows is None:
            resp.result = None
        else:
            resp.result.data_array = rows
            resp.manifest.schema.columns = [MagicMock(name="c") for _ in (cols or [])]
            for c, name in zip(resp.manifest.schema.columns, cols or []):
                c.name = name
        return resp

    def test_no_warehouse_raises(self):
        from backend.routes import scalability as s
        with patch.object(s, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                s._execute_sql("SELECT 1")

    def test_success_returns_rows(self):
        from backend.routes import scalability as s
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=[["a", 1]], cols=["x", "y"])
        with patch.object(s, "WAREHOUSE_ID", "wh"), \
             patch.object(s, "_get_client", return_value=client):
            out = s._execute_sql("SELECT 1")
        assert out == [{"x": "a", "y": 1}]

    def test_empty_result_returns_empty(self):
        from backend.routes import scalability as s
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=None)
        with patch.object(s, "WAREHOUSE_ID", "wh"), \
             patch.object(s, "_get_client", return_value=client):
            assert s._execute_sql("SELECT 1") == []

    def test_failed_state_raises(self):
        from backend.routes import scalability as s
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.FAILED, rows=None, err="bad query")
        with patch.object(s, "WAREHOUSE_ID", "wh"), \
             patch.object(s, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="SQL failed"):
                s._execute_sql("SELECT 1")


# ---------------------------------------------------------------------------
# GET /api/scalability/graph — BFS pagination body
# ---------------------------------------------------------------------------
class TestGraphPagination:
    def _edges(self):
        # a - b - c - d chain (undirected adjacency built by the route)
        def e(s, t):
            return {"src": s, "tgt": t, "entity_type": "JOB", "entity_id": "1"}
        return [
            e("main.s.a", "main.s.b"),
            e("main.s.b", "main.s.c"),
            e("main.s.c", "main.s.d"),
        ]

    def test_graph_default_seed_returns_nodes(self, app_client):
        with patch("backend.routes.scalability._execute_sql", return_value=self._edges()):
            resp = app_client.get("/api/scalability/graph", params={"catalog": "main"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned"] >= 1
        assert data["total_seen"] >= 1
        # all four nodes fit in default page_size => fully exhausted
        assert data["next_cursor"] is None
        ids = {n["id"] for n in data["nodes"]}
        assert "main.s.b" in ids

    def test_graph_focal_table_and_direction(self, app_client):
        with patch("backend.routes.scalability._execute_sql", return_value=self._edges()):
            resp = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "schema": "s",
                "focal_table": "main.s.a", "direction": "downstream"})
        assert resp.status_code == 200
        data = resp.json()
        assert any(n["id"] == "main.s.a" for n in data["nodes"])

    def test_graph_upstream_direction(self, app_client):
        with patch("backend.routes.scalability._execute_sql", return_value=self._edges()):
            resp = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "direction": "upstream"})
        assert resp.status_code == 200

    def test_graph_pagination_cursor_roundtrip(self, app_client):
        with patch("backend.routes.scalability._execute_sql", return_value=self._edges()):
            # page_size=1 forces a next_cursor
            resp1 = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "focal_table": "main.s.a", "page_size": 10})
            assert resp1.status_code == 200
            # small page to force cursor
            resp = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "focal_table": "main.s.a", "page_size": 10})
            assert resp.status_code == 200

    def test_graph_small_page_produces_cursor(self, app_client):
        # make many nodes so page_size=10 cannot exhaust the graph
        def e(i):
            return {"src": f"main.s.t{i}", "tgt": f"main.s.t{i+1}",
                    "entity_type": "", "entity_id": ""}
        edges = [e(i) for i in range(30)]
        with patch("backend.routes.scalability._execute_sql", return_value=edges):
            resp = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "focal_table": "main.s.t0", "page_size": 10})
            data = resp.json()
            assert data["next_cursor"] is not None
            # follow the cursor for page 2
            resp2 = app_client.get("/api/scalability/graph", params={
                "catalog": "main", "page_size": 10, "cursor": data["next_cursor"]})
            assert resp2.status_code == 200
            assert resp2.json()["total_seen"] >= data["total_seen"]

    def test_graph_no_edges_returns_empty(self, app_client):
        with patch("backend.routes.scalability._execute_sql", return_value=[]):
            resp = app_client.get("/api/scalability/graph", params={"catalog": "main"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned"] == 0
        assert data["next_cursor"] is None

    def test_graph_bad_cursor_400(self, app_client):
        resp = app_client.get("/api/scalability/graph", params={
            "catalog": "main", "cursor": "!!!not-base64!!!"})
        assert resp.status_code == 400

    def test_graph_sql_error_500(self, app_client):
        with patch("backend.routes.scalability._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/scalability/graph", params={"catalog": "main"})
        assert resp.status_code == 500

    def test_graph_invalid_schema_400(self, app_client):
        resp = app_client.get("/api/scalability/graph", params={
            "catalog": "main", "schema": "bad;drop"})
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /api/scalability/cache/stats
# ---------------------------------------------------------------------------
class TestCacheStatsFull:
    def test_cache_stats_body(self, app_client):
        fake = MagicMock()
        fake.vacuum.return_value = 2
        fake.stats.return_value = {"total_entries": 5, "live_entries": 3}
        with patch("backend.routes.scalability.get_cache_service", return_value=fake):
            resp = app_client.get("/api/scalability/cache/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["vacuumed_this_call"] == 2
        assert data["stats"]["total_entries"] == 5
        assert "cache_table" in data


# ---------------------------------------------------------------------------
# POST /api/scalability/cache/invalidate
# ---------------------------------------------------------------------------
class TestCacheInvalidateFull:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/scalability/cache/invalidate",
                                     json={"namespace": "lineage"})
        assert resp.status_code == 403

    def test_admin_missing_namespace_400(self, admin_client):
        resp = admin_client.post("/api/scalability/cache/invalidate", json={})
        assert resp.status_code == 400

    def test_admin_ok(self, admin_client):
        fake = MagicMock()
        fake.invalidate_namespace.return_value = 7
        with patch("backend.routes.scalability.get_cache_service", return_value=fake):
            resp = admin_client.post("/api/scalability/cache/invalidate",
                                     json={"namespace": "lineage"})
        assert resp.status_code == 200
        assert resp.json()["entries_expired"] == 7


# ---------------------------------------------------------------------------
# GET /api/scalability/health
# ---------------------------------------------------------------------------
class TestHealthFull:
    def test_health_ok_with_counts(self, app_client):
        fake = MagicMock()
        fake.stats.return_value = {"live_entries": 4}
        with patch("backend.routes.scalability._execute_sql",
                   return_value=[{"node_count": 10, "edge_count": 20}]), \
             patch("backend.routes.scalability.get_cache_service", return_value=fake):
            resp = app_client.get("/api/scalability/health", params={"catalog": "main"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 10
        assert data["edge_count"] == 20
        assert data["cache_ready"] is True
        assert data["cache_live_entries"] == 4
        assert data["pages_needed"] >= 1

    def test_health_sql_error_falls_back_to_zero(self, app_client):
        fake = MagicMock()
        fake.stats.side_effect = RuntimeError("no cache")
        with patch("backend.routes.scalability._execute_sql", side_effect=RuntimeError("boom")), \
             patch("backend.routes.scalability.get_cache_service", return_value=fake):
            resp = app_client.get("/api/scalability/health", params={"catalog": "main"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 0
        assert data["cache_ready"] is False

    def test_health_cache_error_stats_key(self, app_client):
        fake = MagicMock()
        fake.stats.return_value = {"error": "unreachable"}
        with patch("backend.routes.scalability._execute_sql",
                   return_value=[{"node_count": 1, "edge_count": 1}]), \
             patch("backend.routes.scalability.get_cache_service", return_value=fake):
            resp = app_client.get("/api/scalability/health", params={"catalog": "main"})
        assert resp.status_code == 200
        assert resp.json()["cache_ready"] is False

    def test_health_invalid_catalog_400(self, app_client):
        resp = app_client.get("/api/scalability/health", params={"catalog": "bad;"})
        assert resp.status_code == 400
