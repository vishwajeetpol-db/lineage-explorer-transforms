"""Tests for backend/routes/notifications.py.

Covers: list, unread-count, mark-read, scan (with detection helpers),
rules CRUD, plus the module-level _execute_sql helper and _ensure_tables.
All SQL is mocked (patch the module's own _execute_sql / _get_client); offline.
The _tables_ensured global is reset per test so _lazy_ensure is deterministic.
"""
from unittest.mock import MagicMock, patch

import pytest

from databricks.sdk.service.sql import StatementState


@pytest.fixture(autouse=True)
def _reset_ensured():
    """notifications caches a _tables_ensured flag; reset so each test sees a
    clean slate and _lazy_ensure calls are patched away."""
    import backend.routes.notifications as n
    n._tables_ensured = True  # skip CREATE TABLE side-effects by default
    yield
    n._tables_ensured = True


def _patch_sql(return_value=None, side_effect=None):
    if side_effect is not None:
        return patch("backend.routes.notifications._execute_sql", side_effect=side_effect)
    return patch("backend.routes.notifications._execute_sql",
                 return_value=return_value if return_value is not None else [])


# ---------------------------------------------------------------------------
# GET /api/notifications
# ---------------------------------------------------------------------------
class TestList:
    def test_list_default(self, app_client):
        rows = [{"notif_id": "1", "title": "x", "is_read": False}]
        with _patch_sql(return_value=rows):
            resp = app_client.get("/api/notifications")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["notifications"] == rows

    def test_list_with_filters(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.get("/api/notifications", params={
                "notif_type": "schema_change", "severity": "critical",
                "unread_only": True, "limit": 10})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "notif_type = 'schema_change'" in sql
        assert "severity = 'critical'" in sql
        assert "is_read = false" in sql
        assert "LIMIT 10" in sql

    def test_list_limit_out_of_bounds_422(self, app_client):
        resp = app_client.get("/api/notifications", params={"limit": 9999})
        assert resp.status_code == 422

    def test_list_sql_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET /api/notifications/unread-count
# ---------------------------------------------------------------------------
class TestUnreadCount:
    def test_count_ok(self, app_client):
        with _patch_sql(return_value=[{"cnt": 7}]):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 7

    def test_count_empty_rows(self, app_client):
        with _patch_sql(return_value=[]):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.json()["count"] == 0

    def test_count_error_returns_zero(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# POST /api/notifications/mark-read
# ---------------------------------------------------------------------------
class TestMarkRead:
    def test_mark_all(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read", json={"all": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert "is_read = false" in m.call_args[0][0]

    def test_mark_specific_ids(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read",
                                   json={"notif_ids": ["a", "b'c"]})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "notif_id IN (" in sql
        assert "'b''c'" in sql  # single-quote escaped

    def test_mark_noop_when_empty(self, app_client):
        # no ids and all=False => returns ok without SQL error
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/mark-read", json={})
        assert resp.status_code == 200
        assert m.call_count == 0

    def test_mark_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/notifications/mark-read", json={"all": True})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/notifications/scan
# ---------------------------------------------------------------------------
class TestScan:
    def test_scan_detects_and_creates(self, app_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes", return_value=[{"type": "schema_change"}]), \
             patch.object(n, "_detect_dq_degradation", return_value=[{"type": "dq_degradation"}]), \
             patch.object(n, "_detect_sensitive_flows", return_value=[{"type": "sensitive_flow"}]), \
             patch.object(n, "_create_notification") as mk:
            resp = app_client.post("/api/notifications/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["detected"] == {
            "schema_changes": 1, "dq_degradation": 1, "sensitive_flows": 1}
        assert mk.call_count == 3

    def test_scan_error_500(self, app_client):
        import backend.routes.notifications as n
        with patch.object(n, "_detect_schema_changes", side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/notifications/scan")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# GET/POST /api/notifications/rules  + DELETE
# ---------------------------------------------------------------------------
class TestRules:
    def test_list_rules(self, app_client):
        rows = [{"rule_id": "r1", "rule_type": "dq_degradation"}]
        with _patch_sql(return_value=rows):
            resp = app_client.get("/api/notifications/rules")
        assert resp.status_code == 200
        assert resp.json()["rules"] == rows

    def test_list_rules_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/notifications/rules")
        assert resp.status_code == 500

    def test_create_rule_new_id(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/rules",
                                   json={"rule_type": "schema_change"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["rule_id"]  # generated uuid
        assert "MERGE INTO" in m.call_args[0][0]

    def test_upsert_rule_existing_id(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.post("/api/notifications/rules", json={
                "rule_id": "fixed-id", "rule_type": "dq_degradation",
                "target_pattern": "main.*", "threshold": 0.8,
                "severity": "critical", "enabled": False, "notes": "o'brien"})
        assert resp.status_code == 200
        assert resp.json()["rule_id"] == "fixed-id"
        sql = m.call_args[0][0]
        assert "o''brien" in sql       # escaped
        assert "enabled = false" in sql

    def test_create_rule_missing_type_422(self, app_client):
        resp = app_client.post("/api/notifications/rules", json={})
        assert resp.status_code == 422

    def test_upsert_rule_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/notifications/rules",
                                   json={"rule_type": "schema_change"})
        assert resp.status_code == 500

    def test_delete_rule(self, app_client):
        with _patch_sql(return_value=[]) as m:
            resp = app_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 200
        assert "DELETE FROM" in m.call_args[0][0]

    def test_delete_rule_error_500(self, app_client):
        with _patch_sql(side_effect=RuntimeError("boom")):
            resp = app_client.delete("/api/notifications/rules/r1")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Detection helpers (unit) + _create_notification + _ensure_tables/_lazy_ensure
# ---------------------------------------------------------------------------
class TestDetectionHelpers:
    def test_detect_schema_changes(self):
        import backend.routes.notifications as n
        rows = [{"table_catalog": "c", "table_schema": "s", "table_name": "t",
                 "column_name": "col", "data_type": "INT"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_schema_changes()
        assert out[0]["type"] == "schema_change"
        assert out[0]["table_fqn"] == "c.s.t"

    def test_detect_schema_changes_error_returns_empty(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("no perms")):
            assert n._detect_schema_changes() == []

    def test_detect_dq_degradation(self):
        import backend.routes.notifications as n
        rows = [{"rule_id": "r1", "rule_type": "not_null", "table_fqn": "c.s.t",
                 "column_name": "col"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_dq_degradation()
        assert out[0]["type"] == "dq_degradation"

    def test_detect_dq_degradation_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("x")):
            assert n._detect_dq_degradation() == []

    def test_detect_sensitive_flows(self):
        import backend.routes.notifications as n
        rows = [{"source_fqn": "c.s.a", "source_column_name": "email",
                 "target_fqn": "c.s.b", "target_column_name": "email"}]
        with patch.object(n, "_execute_sql", return_value=rows):
            out = n._detect_sensitive_flows()
        assert out[0]["severity"] == "critical"

    def test_detect_sensitive_flows_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("x")):
            assert n._detect_sensitive_flows() == []

    def test_create_notification(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", return_value=[]) as m:
            n._create_notification({"type": "schema_change", "title": "t'x",
                                    "detail": "d", "table_fqn": "c.s.t",
                                    "column_name": "col", "metadata": "{}"})
        assert "INSERT INTO" in m.call_args[0][0]
        assert "t''x" in m.call_args[0][0]

    def test_ensure_tables_creates(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", return_value=[]) as m:
            n._ensure_tables()
        assert m.call_count == 2  # notifications + rules tables

    def test_ensure_tables_swallows_error(self):
        import backend.routes.notifications as n
        with patch.object(n, "_execute_sql", side_effect=RuntimeError("denied")):
            n._ensure_tables()  # must not raise

    def test_lazy_ensure_runs_once(self):
        import backend.routes.notifications as n
        n._tables_ensured = False
        with patch.object(n, "_ensure_tables") as m:
            n._lazy_ensure()
            n._lazy_ensure()
        assert m.call_count == 1
        assert n._tables_ensured is True


# ---------------------------------------------------------------------------
# _execute_sql module helper
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
            resp.manifest.schema.columns = [MagicMock() for _ in (cols or [])]
            for c, name in zip(resp.manifest.schema.columns, cols or []):
                c.name = name
        return resp

    def test_no_warehouse(self):
        import backend.routes.notifications as n
        with patch.object(n, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                n._execute_sql("SELECT 1")

    def test_success(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=[["a"]], cols=["x"])
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            assert n._execute_sql("SELECT 1") == [{"x": "a"}]

    def test_empty(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=None)
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            assert n._execute_sql("SELECT 1") == []

    def test_failed(self):
        import backend.routes.notifications as n
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.FAILED, rows=None, err="bad")
        with patch.object(n, "WAREHOUSE_ID", "wh"), \
             patch.object(n, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="SQL failed"):
                n._execute_sql("SELECT 1")
