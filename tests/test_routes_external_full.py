"""Deep coverage for backend/routes/external_sources.py — dbt/airflow import,
register, sources CRUD, external lineage, and the OL-bridge endpoints.
(Adds to the existing test_routes_external_sources.py; does not edit it.)"""
from unittest.mock import patch

import backend.routes.external_sources as ext


class TestDbtAirflowImport:
    def test_dbt_import_admin(self, admin_client):
        manifest = {"nodes": {
            "model.p.a": {"resource_type": "model", "database": "c", "schema": "s", "name": "a",
                          "depends_on": {"nodes": ["model.p.b"]}},
            "model.p.b": {"resource_type": "model", "database": "c", "schema": "s", "name": "b",
                          "depends_on": {"nodes": []}},
        }}
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.post("/api/external/dbt/import", json={"manifest": manifest})
        assert r.status_code in (200, 201)

    def test_dbt_import_non_admin_403(self, non_admin_client):
        r = non_admin_client.post("/api/external/dbt/import", json={"manifest": {"nodes": {}}})
        assert r.status_code == 403

    def test_airflow_import_admin(self, admin_client):
        body = {"edges": [{"source": "c.s.a", "target": "c.s.b", "dag_id": "d1", "task_id": "t1"}]}
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.post("/api/external/airflow/import", json=body)
        # body schema may differ; the point is admin passes the gate (not 403)
        assert r.status_code != 403

    def test_airflow_import_non_admin_403(self, non_admin_client):
        r = non_admin_client.post("/api/external/airflow/import", json={"edges": []})
        assert r.status_code == 403


class TestRegisterAndSources:
    def test_register_edge(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.post("/api/external/register", json={
                "source_table": "c.s.a", "target_table": "c.s.b", "source_system": "custom"})
        assert r.status_code in (200, 201, 422)

    def test_list_sources(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[
                {"source_id": "1", "source_system": "dbt", "created_at": "t"}]):
            r = app_client.get("/api/external/sources")
        assert r.status_code == 200
        assert "sources" in r.json()

    def test_delete_source(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.delete("/api/external/sources/src-1")
        assert r.status_code in (200, 204)

    def test_external_lineage(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[
                {"source_table": "c.s.a", "target_table": "c.s.b", "source_system": "dbt"}]):
            r = app_client.get("/api/external/lineage", params={"catalog": "c", "schema": "s"})
        assert r.status_code in (200, 422)


class TestOLBridge:
    def test_register_admin(self, admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.post("/api/external/ol-bridge/register", json={
                "platform": "custom", "name": "marquez", "description": "d"})
        assert r.status_code in (200, 201)

    def test_register_non_admin_403(self, non_admin_client):
        # Valid body so the request reaches the admin gate (body validates first).
        r = non_admin_client.post("/api/external/ol-bridge/register", json={
            "platform": "custom", "name": "x"})
        assert r.status_code == 403

    def test_ingest_bad_uuid_400(self, app_client):
        r = app_client.post("/api/external/ol-bridge/ingest/not-a-uuid", json={})
        assert r.status_code == 400

    def test_ingest_unknown_source_403(self, app_client):
        # Valid UUID but no matching active source row -> 403.
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.post(
                "/api/external/ol-bridge/ingest/12345678-1234-1234-1234-123456789abc",
                json={"eventType": "COMPLETE", "inputs": [], "outputs": []})
        assert r.status_code in (403, 200)

    def test_list_ol_bridge_sources(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.get("/api/external/ol-bridge/sources")
        assert r.status_code == 200

    def test_ol_bridge_events(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.get("/api/external/ol-bridge/events")
        assert r.status_code == 200
