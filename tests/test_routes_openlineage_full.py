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
    def test_import_valid_events(self, admin_client):
        body = {"events": [{
            "eventType": "COMPLETE",
            "eventTime": "2024-01-01T00:00:00Z",
            "job": {"namespace": "databricks", "name": "j/1"},
            "run": {"runId": "r1"},
            "inputs": [{"namespace": "databricks://cat.sch", "name": "src"}],
            "outputs": [{"namespace": "databricks://cat.sch", "name": "tgt"}],
        }]}
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        # first call is CREATE TABLE, later is INSERT
        assert any("INSERT INTO" in c[0][0] for c in m.call_args_list)

    def test_import_no_events_400(self, admin_client):
        resp = admin_client.post("/api/import/openlineage", json={"events": []})
        assert resp.status_code == 400

    def test_import_sql_error_500(self, admin_client):
        body = {"events": [{"eventType": "COMPLETE"}]}
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 500

    def test_import_requires_admin(self, non_admin_client):
        """Ungated ingest let any app user inject trusted-looking lineage."""
        body = {"events": [{"eventType": "COMPLETE"}]}
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = non_admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 403
        m.assert_not_called()

    def test_import_escapes_backslash_before_quote(self, admin_client):
        r"""`\'` must not close the literal — sql_str doubles the backslash first."""
        body = {"events": [{
            "eventType": "COMPLETE",
            "eventTime": "2024-01-01T00:00:00Z",
            # backslash + quote: quote-doubling alone would break out here
            "job": {"namespace": "ns", "name": "j\\' OR 1=1 --"},
            "run": {"runId": "r1"},
        }]}
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 200
        sql = next(c[0][0] for c in m.call_args_list if "INSERT INTO" in c[0][0])
        # backslash doubled AND quote doubled -> payload stays inside the literal
        assert "j\\\\'' OR 1=1 --" in sql
        assert "j\\'' OR 1=1" not in sql

    def test_import_error_detail_not_leaked(self, admin_client):
        """500s must not echo raw SQL error text back to the caller."""
        body = {"events": [{"eventType": "COMPLETE"}]}
        with patch("backend.routes.openlineage._execute_sql",
                   side_effect=RuntimeError("SQL failed: secret-table not found")):
            resp = admin_client.post("/api/import/openlineage", json=body)
        assert resp.status_code == 500
        assert "secret-table" not in resp.text
        assert "SQL failed" not in resp.text


# ---------------------------------------------------------------------------
# Producer configure / config
# ---------------------------------------------------------------------------
class TestProducerConfigure:
    @staticmethod
    def _merge_sql(mock):
        """The MERGE statement, whichever call it was.

        configure_producer now issues a second statement after the MERGE to read
        the stored config_id back, so `call_args` (the LAST call) is the SELECT.
        """
        for call in mock.call_args_list:
            if "MERGE INTO" in call[0][0]:
                return call[0][0]
        raise AssertionError(f"no MERGE issued; calls={[c[0][0][:60] for c in mock.call_args_list]}")

    def test_configure_ok(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "Marquez",
                "api_key_secret_scope": "sc", "api_key_secret_key": "k"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["endpoint_name"] == "Marquez"
        assert "MERGE INTO" in self._merge_sql(m)

    def test_configure_missing_url_400(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_name": "x"})
        assert resp.status_code == 400

    def test_configure_sql_error_500(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://x.com"})
        assert resp.status_code == 500

    def test_configure_requires_admin(self, non_admin_client):
        """Ungated, the endpoint_name-keyed MERGE let any user re-point delivery."""
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = non_admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://attacker.example.com/collect",
                "endpoint_name": "Marquez"})
        assert resp.status_code == 403
        m.assert_not_called()

    @pytest.mark.parametrize("url", [
        "http://marquez.example.com/api/v1/lineage",  # plaintext
        "file:///etc/passwd",                          # non-http scheme
        "https://",                                    # scheme but no host
        "https:///api/v1/lineage",                     # empty host
        "marquez.example.com/api/v1/lineage",          # no scheme at all
    ])
    def test_configure_rejects_non_https_url(self, admin_client, url):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": url, "endpoint_name": "Marquez"})
        assert resp.status_code == 400
        assert "https" in resp.json()["detail"]
        m.assert_not_called()

    def test_configure_omitting_secret_refs_leaves_them_alone(self, admin_client):
        """An update that does NOT mention the secret must not touch it.

        This is the property the original "never update on MATCHED" rule was
        reaching for, expressed correctly: omission preserves. The blanket rule
        also blocked rotation, which made a secret reference write-once-forever —
        see test_configure_can_rotate_secret_refs.
        """
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "Marquez"})
        assert resp.status_code == 200
        assert resp.json()["secret_rotated"] is False
        sql = self._merge_sql(m)
        update_clause = sql.split("WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
        assert "api_key_secret" not in update_clause
        # still set on first registration
        assert "api_key_secret_scope" in sql.split("WHEN NOT MATCHED")[1]

    def test_configure_can_rotate_secret_refs(self, admin_client):
        """Supplying the secret refs on an existing endpoint must rotate them.

        Previously WHEN MATCHED ignored these columns unconditionally, so the
        ordinary "register now, add auth later" flow silently discarded the secret
        and returned 200 — and with no DELETE endpoint or row-replace path for
        this table, the documented "delete and re-create" workaround did not
        exist. The endpoint is admin-gated, so rotation is a legitimate action.
        """
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "Marquez",
                "api_key_secret_scope": "new-scope",
                "api_key_secret_key": "new-key"})
        assert resp.status_code == 200
        assert resp.json()["secret_rotated"] is True
        update_clause = self._merge_sql(m).split(
            "WHEN MATCHED THEN UPDATE SET")[1].split("WHEN NOT MATCHED")[0]
        assert "api_key_secret_scope = 'new-scope'" in update_clause
        assert "api_key_secret_key = 'new-key'" in update_clause

    def test_configure_returns_the_stored_config_id_not_a_fresh_one(self, admin_client):
        """On the MATCHED path the handler used to return a freshly minted UUID
        that was never written to any row."""
        with patch("backend.routes.openlineage._execute_sql",
                   return_value=[{"config_id": "already-stored-id"}]):
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "Marquez"})
        assert resp.status_code == 200
        assert resp.json()["config_id"] == "already-stored-id"

    def test_configure_escapes_endpoint_name(self, admin_client):
        r"""endpoint_name feeds the MERGE's USING clause — `\'` must stay inert."""
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/configure", json={
                "endpoint_url": "https://marquez.example.com/api/v1/lineage",
                "endpoint_name": "x\\' AS endpoint_name) s ON true --"})
        assert resp.status_code == 200
        sql = m.call_args[0][0]
        assert "x\\\\'' AS endpoint_name) s ON true --" in sql

    def test_get_config(self, admin_client):
        rows = [{"config_id": "c1", "endpoint_name": "Marquez", "active": True}]
        with patch("backend.routes.openlineage._execute_sql", return_value=rows):
            resp = admin_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 200
        assert resp.json()["endpoints"] == rows

    def test_get_config_requires_admin(self, non_admin_client):
        """The commit message claimed this was admin-gated; it was not."""
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = non_admin_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 403
        m.assert_not_called()

    def test_get_config_redacts_endpoint_url(self, admin_client):
        """endpoint_url can carry a credential in its query string, and
        configure_producer only checks https+host — so the stored value is
        reduced to scheme://host on the way out, exactly as the sibling webhook
        list already does."""
        rows = [{"config_id": "c1", "endpoint_name": "Marquez",
                 "endpoint_url": "https://marquez.internal.corp/api/v1/lineage?apiKey=SECRET"}]
        with patch("backend.routes.openlineage._execute_sql", return_value=rows):
            resp = admin_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 200
        url = resp.json()["endpoints"][0]["endpoint_url"]
        assert url == "https://marquez.internal.corp"
        assert "SECRET" not in resp.text
        assert "apiKey" not in resp.text

    def test_get_config_error_500(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = admin_client.get("/api/openlineage/producer/config")
        assert resp.status_code == 500
        assert "boom" not in resp.text


# ---------------------------------------------------------------------------
# Producer produce
# ---------------------------------------------------------------------------
class TestProducerProduce:
    def test_produce_builds_and_queues(self, admin_client):
        writes = [{
            "source_table_full_name": "cat.sch.src",
            "target_table_full_name": "cat.sch.tgt",
            "entity_type": "JOB", "entity_id": "42",
            "event_time": "2024-01-01T00:00:00Z",
        }]
        with patch("backend.routes.openlineage._execute_sql", return_value=writes) as m:
            resp = admin_client.post("/api/openlineage/producer/produce", params={
                "catalog": "cat", "schema": "sch", "lookback_hours": 24})
        assert resp.status_code == 200
        data = resp.json()
        assert data["events_produced"] == 1
        assert any("INSERT INTO" in c[0][0] for c in m.call_args_list)

    def test_produce_skips_non_three_part_target(self, admin_client):
        writes = [{
            "source_table_full_name": "cat.sch.src",
            "target_table_full_name": "not_three_parts",
            "entity_type": "JOB", "entity_id": "42",
            "event_time": "2024-01-01T00:00:00Z",
        }]
        with patch("backend.routes.openlineage._execute_sql", return_value=writes):
            resp = admin_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 200
        assert resp.json()["events_produced"] == 0

    def test_produce_no_writes(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]):
            resp = admin_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 200
        assert resp.json()["events_produced"] == 0

    def test_produce_lookback_out_of_range_422(self, admin_client):
        resp = admin_client.post("/api/openlineage/producer/produce", params={
            "catalog": "cat", "lookback_hours": 999})
        assert resp.status_code == 422

    def test_produce_error_500(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 500

    def test_produce_error_detail_not_leaked(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql",
                   side_effect=RuntimeError("SQL failed: system.access.table_lineage denied")):
            resp = admin_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 500
        assert "system.access" not in resp.text
        assert "SQL failed" not in resp.text

    def test_produce_requires_admin(self, non_admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = non_admin_client.post("/api/openlineage/producer/produce", params={"catalog": "cat"})
        assert resp.status_code == 403
        m.assert_not_called()

    def test_produce_rejects_union_injection_in_catalog(self, admin_client):
        """The confirmed 5-column UNION exploit against system.access.table_lineage."""
        payload = ("x' AND 1=0 UNION SELECT 'a.b.c','a.b.c','JOB','1',current_timestamp() "
                   "FROM system.access.audit -- ")
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/produce", params={
                "catalog": payload})
        assert resp.status_code == 400
        assert "Invalid catalog" in resp.json()["detail"]
        m.assert_not_called()

    def test_produce_rejects_injection_in_schema(self, admin_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/produce", params={
                "catalog": "cat", "schema": "sch' OR '1'='1"})
        assert resp.status_code == 400
        assert "Invalid schema" in resp.json()["detail"]
        m.assert_not_called()

    def test_produce_accepts_hyphenated_identifiers(self, admin_client):
        """UC allows hyphens — the allow-list must not reject legitimate names."""
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = admin_client.post("/api/openlineage/producer/produce", params={
                "catalog": "my-catalog", "schema": "adi-413"})
        assert resp.status_code == 200
        sql = m.call_args_list[0][0][0]
        assert "target_table_catalog = 'my-catalog'" in sql
        assert "target_table_schema = 'adi-413'" in sql


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

    @pytest.mark.parametrize("status", ["pending", "delivered", "failed"])
    def test_events_accepts_known_statuses(self, app_client, status):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = app_client.get("/api/openlineage/producer/events", params={
                "status_filter": status})
        assert resp.status_code == 200
        assert any(f"status = '{status}'" in c[0][0] for c in m.call_args_list)

    def test_events_rejects_injection_status_filter(self, app_client):
        """status_filter reached a SELECT whose rows are returned to the caller."""
        payload = "pending' UNION SELECT api_key_secret_key,'','','','','' FROM x -- "
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = app_client.get("/api/openlineage/producer/events", params={
                "status_filter": payload})
        assert resp.status_code == 400
        assert "must be one of" in resp.json()["detail"]
        m.assert_not_called()

    def test_events_rejects_unknown_status(self, app_client):
        with patch("backend.routes.openlineage._execute_sql", return_value=[]) as m:
            resp = app_client.get("/api/openlineage/producer/events", params={
                "status_filter": "queued"})
        assert resp.status_code == 400
        m.assert_not_called()

    def test_events_error_detail_not_leaked(self, app_client):
        with patch("backend.routes.openlineage._execute_sql",
                   side_effect=RuntimeError("SQL failed: openlineage_producer_queue missing")):
            resp = app_client.get("/api/openlineage/producer/events")
        assert resp.status_code == 500
        assert "SQL failed" not in resp.text
        assert "openlineage_producer_queue" not in resp.text


# ---------------------------------------------------------------------------
# HTTPExceptions raised inside a handler body keep their status
# ---------------------------------------------------------------------------
class TestHTTPExceptionNotMasked:
    """A 4xx from a deeper layer must not be rewritten as a generic 500 —
    otherwise a validation/authz error surfaces as a server fault."""

    def test_export_preserves_downstream_400(self, app_client):
        from fastapi import HTTPException
        with patch("backend.routes.openlineage.get_table_lineage",
                   side_effect=HTTPException(status_code=400, detail="Invalid catalog: 'x'")):
            resp = app_client.get("/api/export/openlineage", params={"catalog": "cat"})
        assert resp.status_code == 400

    @pytest.mark.parametrize("method,path,kwargs", [
        ("post", "/api/import/openlineage", {"json": {"events": [{"eventType": "COMPLETE"}]}}),
        ("post", "/api/openlineage/producer/configure",
         {"json": {"endpoint_url": "https://x.example.com"}}),
        ("post", "/api/openlineage/producer/produce", {"params": {"catalog": "cat"}}),
    ])
    def test_producer_handlers_preserve_400(self, admin_client, method, path, kwargs):
        from fastapi import HTTPException
        with patch("backend.routes.openlineage._execute_sql",
                   side_effect=HTTPException(status_code=400, detail="nope")):
            resp = getattr(admin_client, method)(path, **kwargs)
        assert resp.status_code == 400


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
