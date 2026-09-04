"""Targeted top-ups for the last uncovered branches (validation/error paths)
to keep the backend coverage gate comfortably above 90%."""
from unittest.mock import patch, MagicMock

import pytest


class TestDiscoveryValidation:
    def test_sensitive_bad_catalog_400(self, app_client):
        r = app_client.get("/api/discover/sensitive", params={"catalog": "bad;"})
        assert r.status_code == 400

    def test_sensitive_bad_schema_400(self, app_client):
        r = app_client.get("/api/discover/sensitive", params={"catalog": "c", "schema": "bad;"})
        assert r.status_code == 400

    def test_orphans_bad_catalog_400(self, app_client):
        r = app_client.get("/api/discover/orphans", params={"catalog": "bad;"})
        assert r.status_code == 400


class TestRootCauseBranches:
    def test_invalid_anomaly_timestamp_400(self, app_client):
        r = app_client.post("/api/root-cause/analyze", json={
            "catalog": "c", "schema_name": "s", "table": "t", "column": "x",
            "anomaly_timestamp": "not-a-date"})
        assert r.status_code == 400

    def test_valid_anomaly_timestamp_ok(self, app_client):
        with patch("backend.routes.root_cause.trace_root_cause", return_value={"candidates": []}):
            r = app_client.post("/api/root-cause/analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t", "column": "x",
                "anomaly_timestamp": "2026-07-01T00:00:00Z"})
        assert r.status_code == 200

    def test_service_error_500(self, app_client):
        with patch("backend.routes.root_cause.trace_root_cause", side_effect=RuntimeError("boom")):
            r = app_client.post("/api/root-cause/analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t", "column": "x"})
        assert r.status_code == 500


class TestCapabilityCacheSql:
    def _svc(self):
        from backend.server.capability_cache import CapabilityCache
        return CapabilityCache()

    def test_sql_no_warehouse_raises(self):
        from backend.server import capability_cache as cc
        with patch.object(cc, "WAREHOUSE_ID", ""):
            with pytest.raises(Exception):
                self._svc()._sql("SELECT 1")

    def test_sql_success_maps_rows(self):
        from backend.server import capability_cache as cc
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        resp = client.statement_execution.execute_statement.return_value
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["v"]]
        col = MagicMock(); col.name = "c"
        resp.manifest.schema.columns = [col]
        with patch.object(cc, "WAREHOUSE_ID", "wh"), \
             patch.object(cc, "_get_client", return_value=client):
            rows = self._svc()._sql("SELECT 1")
        assert rows == [{"c": "v"}]

    def test_sql_failed_state_raises(self):
        from backend.server import capability_cache as cc
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        resp = client.statement_execution.execute_statement.return_value
        resp.status.state = StatementState.FAILED
        resp.status.error.message = "nope"
        with patch.object(cc, "WAREHOUSE_ID", "wh"), \
             patch.object(cc, "_get_client", return_value=client):
            with pytest.raises(Exception):
                self._svc()._sql("SELECT 1")

    def test_ensure_table_runs_once(self):
        s = self._svc()
        with patch.object(s, "_sql", return_value=[]) as m:
            s._ensure_table()
            s._ensure_table()  # second call short-circuits on _ready
        assert s._ready is True and m.call_count == 1

    def test_ensure_table_error_non_fatal(self):
        s = self._svc()
        with patch.object(s, "_sql", side_effect=RuntimeError("no perms")):
            s._ensure_table()
        assert s._ready is False

    def test_evict_table_error_returns_zero(self):
        s = self._svc()
        with patch.object(s, "_ensure_table"), patch.object(s, "_sql", side_effect=RuntimeError("x")):
            assert s.evict_table("c.s.t") == 0

    def test_evict_all_error_returns_zero(self):
        s = self._svc()
        with patch.object(s, "_ensure_table"), patch.object(s, "_sql", side_effect=RuntimeError("x")):
            assert s.evict_all() == 0

    def test_inventory_error_returns_empty(self):
        s = self._svc()
        with patch.object(s, "_ensure_table"), patch.object(s, "_sql", side_effect=RuntimeError("x")):
            assert s.inventory() == []


class TestImpactErrorPaths:
    def test_bfs_downstream_error(self):
        import backend.routes.impact as impact
        with patch.object(impact, "_execute_sql", side_effect=RuntimeError("timeout")):
            assert impact._bfs_downstream("c.s.a") == {}

    def test_impact_route_service_error_500(self, app_client):
        import backend.routes.impact as impact
        with patch.object(impact, "_bfs_downstream", side_effect=RuntimeError("boom")):
            r = app_client.get("/api/impact", params={"catalog": "c", "schema": "s", "table": "t"})
        assert r.status_code == 500
