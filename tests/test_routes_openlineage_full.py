"""Deeper tests for backend/routes/openlineage.py — export event building,
import happy path, producer configure/config/produce/events. Complements the
basic test_routes_openlineage.py (left untouched). SQL + get_table_lineage
mocked; offline. Producer-ensured globals reset per test.
"""
from unittest.mock import MagicMock, patch

import pytest

from databricks.sdk.service.sql import StatementState
from backend.models import (
    ColumnLineageEdge,
    ColumnLineageResponse,
    EntityNode,
    LineageEdge,
    LineageResponse,
    TableNode,
)


@pytest.fixture(autouse=True)
def _reset_producer_ensured():
    import backend.routes.openlineage as ol
    ol._producer_tables_ensured = True  # skip CREATE TABLE unless a test opts in
    yield
    ol._producer_tables_ensured = True


def _table(fqn, status="connected"):
    return TableNode(id=fqn, name=fqn.split(".")[-1], full_name=fqn,
                     table_type="MANAGED", lineage_status=status)


def _entity(etype="JOB", eid="42"):
    return EntityNode(id=f"entity:{etype}:{eid}", entity_type=etype, entity_id=eid,
                      display_name="etl")


# ---------------------------------------------------------------------------
# Helper functions (pure)
# ---------------------------------------------------------------------------
def test_table_to_openlineage_dataset():
    import backend.routes.openlineage as ol
    ds = ol._table_to_openlineage_dataset("cat", "sch", "tbl")
    assert ds["namespace"] == "databricks://cat.sch"
    assert ds["name"] == "tbl"
    assert "dataSource" in ds["facets"]


def test_build_openlineage_run_event():
    import backend.routes.openlineage as ol
    ev = ol._build_openlineage_run_event([], {"name": "t"}, "JOB", "1", "2024-01-01T00:00:00Z")
    assert ev["eventType"] == "COMPLETE"
    assert ev["job"]["name"] == "JOB/1"
    assert ev["outputs"] == [{"name": "t"}]


# ---------------------------------------------------------------------------
# GET /api/export/openlineage
# ---------------------------------------------------------------------------
class TestExport:
    def _lineage_with_flow(self):
        src = _table("cat.sch.src")
        tgt = _table("cat.sch.tgt")
        ent = _entity()
        nodes = [src, tgt, ent]
        edges = [
            LineageEdge(source=src.id, target=ent.id),   # table -> entity
            LineageEdge(source=ent.id, target=tgt.id),   # entity -> table
        ]
        return LineageResponse(nodes=nodes, edges=edges)

    def test_export_builds_events(self, app_client):
        with patch("backend.routes.openlineage.get_table_lineage",
                   return_value=self._lineage_with_flow()):
            resp = app_client.get("/api/export/openlineage", params={"catalog": "cat"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        ev = data["events"][0]
        assert ev["outputs"][0]["name"] == "tgt"
        assert any(i["name"] == "src" for i in ev["inputs"])

    def test_export_with_columns(self, app_client):
        col = ColumnLineageResponse(edges=[ColumnLineageEdge(
            source_table="cat.sch.src", source_column="c1",
            target_table="cat.sch.tgt", target_column="tc1")])
        with patch("backend.routes.openlineage.get_table_lineage",
                   return_value=self._lineage_with_flow()), \
             patch("backend.routes.openlineage.get_schema_column_lineage",
                   return_value=col):
            resp = app_client.get("/api/export/openlineage", params={
                "catalog": "cat", "schema": "sch", "include_columns": True})
        assert resp.status_code == 200
        ev = resp.json()["events"][0]
        # schema facet added because target column matched
        assert "schema" in ev["outputs"][0]["facets"]

    def test_export_columns_error_swallowed(self, app_client):
        with patch("backend.routes.openlineage.get_table_lineage",
                   return_value=self._lineage_with_flow()), \
             patch("backend.routes.openlineage.get_schema_column_lineage",
                   side_effect=RuntimeError("no cols")):
            resp = app_client.get("/api/export/openlineage", params={
                "catalog": "cat", "schema": "sch", "include_columns": True})
        assert resp.status_code == 200

    def test_export_empty(self, app_client):
        with patch("backend.routes.openlineage.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])):
            resp = app_client.get("/api/export/openlineage", params={"catalog": "cat"})
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_export_error_500(self, app_client):
        with patch("backend.routes.openlineage.get_table_lineage",
                   side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/export/openlineage", params={"catalog": "cat"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# POST /api/import/openlineage
# ---------------------------------------------------------------------------
class TestImport:
    def test_import_valid_events(self, app_client):
        body = {"events": [{
            "eventType": "COMPLETE",
            "eventTime": "2024-01-01T00:00:00Z",
            "job": {"namespace": "databricks", "name": "j/1"},
            "run": {"runId": "r1"},
            "inputs": [{"namespace": "databricks://cat.sch", "name": "src"}],
            "outputs": [{"namespace": "databricks://cat.sch", "name": "tgt"}],
        }]}
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = app_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        # first call is CREATE TABLE, later is INSERT
        assert any("INSERT INTO" in c[0][0] for c in m.call_args_list)

    def test_import_no_events_400(self, app_client):
        resp = app_client.post("/api/import/openlineage", json={"events": []})
        assert resp.status_code == 400

    def test_import_sql_error_500(self, app_client):
        body = {"events": [{"eventType": "COMPLETE"}]}
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Producer configure / config
# ---------------------------------------------------------------------------
class TestProducerConfigure:
    def test_configure_ok(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = app_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "Marquez",
                "api_key_secret_scope": "sc", "api_key_secret_key": "k"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint_name"] == "Marquez"
        assert "MERGE INTO" in m.call_args[0][0]

    def test_configure_missing_url_400(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.post("/api/openlineage/producer/configure", json={
                "endpoint_name": "x"})
        assert resp.status_code == 400

    def test_configure_sql_error_500(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://x.com"})
        assert resp.status_code == 500

    def test_get_config(self, app_client):
        rows = [{"config_id": "c1", "endpoint_name": "Marquez", "active": True}]
        with patch("backend.routes.openlineage._execute_sql", return_value=rows):
            resp = app_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 200
        assert resp.json()["endpoints"] == rows

    def test_get_config_error_500(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Producer produce
# ---------------------------------------------------------------------------
class TestProducerProduce:
    def test_produce_builds_and_queues(self, app_client):
        writes = [{
            "source_table_full_name": "cat.sch.src",
            "target_table_full_name": "cat.sch.tgt",
            "entity_type": "JOB", "entity_id": "42",
            "event_time": "2024-01-01T00:00:00Z",
        }]
        with patch("backend.routes.openlineage._execute_sql", return_value=writes) as m:
            resp = app_client.post("/api/openlineage/producer/produce", params={
                "catalog": "cat", "schema": "sch", "lookback_hours": 24})
        assert resp.status_code == 200
        data = resp.json()
        assert data["events_produced"] == 1
        assert any("INSERT INTO" in c[0][0] for c in m.call_args_list)

    def test_produce_skips_non_three_part_target(self, app_client):
        writes = [{
            "source_table_full_name": "cat.sch.src",
            "target_table_full_name": "not_three_parts",
            "entity_type": "JOB", "entity_id": "42",
            "event_time": "2024-01-01T00:00:00Z",
        }]
        with patch("backend.routes.openlineage._execute_sql", return_value=writes):
            resp = app_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 200
        assert resp.json()["events_produced"] == 0

    def test_produce_no_writes(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 200
        assert resp.json()["events_produced"] == 0

    def test_produce_lookback_out_of_range_422(self, app_client):
        resp = app_client.post("/api/openlineage/producer/produce", params={
            "catalog": "cat", "lookback_hours": 999})
        assert resp.status_code == 422

    def test_produce_error_500(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Producer events
# ---------------------------------------------------------------------------
class TestProducerEvents:
    def test_events_with_filter_and_counts(self, app_client):
        def fake(sql):
            if "GROUP BY status" in sql:
                return [{"status": "pending", "cnt": 3}, {"status": "delivered", "cnt": 1}]
            return [{"event_id": "e1", "status": "pending"}]
        with patch("backend.routes.openlineage._execute_sql", side_effect=fake) as m:
            resp = app_client.get("/api/openlineage/producer/events", params={
                "status_filter": "pending", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["counts"] == {"pending": 3, "delivered": 1}
        assert data["events"][0]["event_id"] == "e1"
        # status filter applied to the listing query
        assert any("status = 'pending'" in c[0][0] for c in m.call_args_list)

    def test_events_no_filter(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = app_client.get("/api/openlineage/producer/events")
        assert resp.status_code == 200

    def test_events_error_500(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/openlineage/producer/events")
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Producer table ensure helpers
# ---------------------------------------------------------------------------
class TestProducerEnsure:
    def test_ensure_producer_tables(self):
        import backend.routes.openlineage as ol
        with patch.object(ol, "_execute_sql", return_value=[]) as m:
            ol._ensure_producer_tables()
        assert m.call_count == 2

    def test_ensure_producer_tables_swallows(self):
        import backend.routes.openlineage as ol
        with patch.object(ol, "_execute_sql", side_effect=RuntimeError("denied")):
            ol._ensure_producer_tables()  # no raise

    def test_lazy_ensure_producer_once(self):
        import backend.routes.openlineage as ol
        ol._producer_tables_ensured = False
        with patch.object(ol, "_ensure_producer_tables") as m:
            ol._lazy_ensure_producer()
            ol._lazy_ensure_producer()
        assert m.call_count == 1


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
        import backend.routes.openlineage as ol
        with patch.object(ol, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                ol._execute_sql("SELECT 1")

    def test_success(self):
        import backend.routes.openlineage as ol
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=[["a"]], cols=["x"])
        with patch.object(ol, "WAREHOUSE_ID", "wh"), \
             patch.object(ol, "_get_client", return_value=client):
            assert ol._execute_sql("SELECT 1") == [{"x": "a"}]

    def test_empty(self):
        import backend.routes.openlineage as ol
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, rows=None)
        with patch.object(ol, "WAREHOUSE_ID", "wh"), \
             patch.object(ol, "_get_client", return_value=client):
            assert ol._execute_sql("SELECT 1") == []

    def test_failed(self):
        import backend.routes.openlineage as ol
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.FAILED, rows=None, err="bad")
        with patch.object(ol, "WAREHOUSE_ID", "wh"), \
             patch.object(ol, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="SQL failed"):
                ol._execute_sql("SELECT 1")
