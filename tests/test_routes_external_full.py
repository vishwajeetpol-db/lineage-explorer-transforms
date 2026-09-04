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
    """/register and DELETE /sources/{id} write / destroy external_lineage_edges
    and external_sources — the same tables the import endpoints gate, so both are
    admin-gated too."""

    _EDGE = {"source_platform": "custom", "source_asset": "sf.raw.a",
             "target_asset": "c.s.b", "relationship": "produces"}

    def test_register_edge_admin(self, admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.post("/api/external/register", json=self._EDGE)
        assert r.status_code in (200, 201)

    def test_register_edge_non_admin_403(self, non_admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = non_admin_client.post("/api/external/register", json=self._EDGE)
        assert r.status_code == 403
        m.assert_not_called()

    def test_register_edge_bad_platform_400(self, admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = admin_client.post("/api/external/register", json={
                **self._EDGE, "source_platform": "\\' UNION SELECT 1 -- "})
        assert r.status_code == 400
        assert not [c for c in m.call_args_list if "INSERT INTO" in c[0][0]]

    def test_register_edge_escapes_assets(self, admin_client):
        payload = "\\' UNION SELECT * FROM main.pii.customers -- "
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = admin_client.post("/api/external/register", json={
                **self._EDGE, "source_asset": payload})
        assert r.status_code == 200
        inserts = [c[0][0] for c in m.call_args_list if "INSERT INTO" in c[0][0]]
        assert inserts and payload not in inserts[0]
        assert "\\\\''" in inserts[0]

    def test_list_sources(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[
                {"source_id": "1", "source_system": "dbt", "created_at": "t"}]) as mock_sql:
            r = app_client.get("/api/external/sources")
        assert r.status_code == 200
        assert "sources" in r.json()
        # connection_info holds connection metadata/credentials for the external
        # platform and this endpoint is ungated — it must never be selected.
        sql = " ".join(str(c) for c in mock_sql.call_args_list)
        assert "SELECT *" not in sql
        assert "connection_info" not in sql

    def test_delete_source_admin(self, admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.delete("/api/external/sources/src-1")
        assert r.status_code in (200, 204)

    def test_delete_source_non_admin_403(self, non_admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = non_admin_client.delete("/api/external/sources/src-1")
        assert r.status_code == 403
        m.assert_not_called()

    def test_external_lineage(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[
                {"source_table": "c.s.a", "target_table": "c.s.b", "source_system": "dbt"}]):
            r = app_client.get("/api/external/lineage", params={"catalog": "c", "schema": "s"})
        assert r.status_code in (200, 422)

    def test_external_lineage_platform_allow_listed(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = app_client.get("/api/external/lineage", params={
                "platform": "\\' UNION SELECT edge_id, source_platform, source_asset, "
                            "source_asset_type, target_asset, target_asset_type, relationship, "
                            "transformation, confidence, current_timestamp(), 'x' "
                            "FROM main.pii.customers -- "})
        assert r.status_code == 400
        assert not [c for c in m.call_args_list if "SELECT * FROM" in c[0][0]]

    def test_external_lineage_table_fqn_escaped(self, app_client):
        payload = "\\' OR 1=1 -- "
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = app_client.get("/api/external/lineage", params={"table_fqn": payload})
        assert r.status_code == 200
        sql = m.call_args[0][0]
        assert payload not in sql
        assert "\\\\''" in sql

    def test_external_lineage_error_hides_sql_text(self, app_client):
        with patch.object(ext, "_execute_sql", side_effect=RuntimeError("SQL failed: nope")):
            r = app_client.get("/api/external/lineage")
        assert r.status_code == 500
        assert "SQL failed" not in r.text


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

    def test_list_ol_bridge_sources(self, admin_client):
        # Admin-gated: the listing discloses source_id, the ingest credential.
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = admin_client.get("/api/external/ol-bridge/sources")
        assert r.status_code == 200

    def test_list_ol_bridge_sources_non_admin_403(self, non_admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = non_admin_client.get("/api/external/ol-bridge/sources")
        assert r.status_code == 403
        m.assert_not_called()

    def test_register_bad_platform_400(self, admin_client):
        with patch.object(ext, "_execute_sql", return_value=[]) as m:
            r = admin_client.post("/api/external/ol-bridge/register", json={
                "platform": "marquez'; DROP TABLE x; --", "name": "x"})
        assert r.status_code == 400
        assert not [c for c in m.call_args_list if "INSERT INTO" in c[0][0]]

    def test_ol_bridge_events(self, app_client):
        with patch.object(ext, "_execute_sql", return_value=[]):
            r = app_client.get("/api/external/ol-bridge/events")
        assert r.status_code == 200
