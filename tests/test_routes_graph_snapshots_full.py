"""Coverage-focused tests for backend/routes/graph_snapshots.py (cap 07).

Drives capture (with node/edge serialization + oversize guard), list, get
(found / not-found / bad-json), diff (called directly since the HTTP path is
shadowed by /{snapshot_id}), delete, and the module helpers _execute_sql,
_ensure_table, _lazy_ensure. No backend/ edits; everything mocked; fast+offline.
"""
import asyncio
from unittest.mock import MagicMock, patch

import pytest


def _lineage_with_entity():
    from backend.models import EntityNode, LineageEdge, LineageResponse
    node = EntityNode(id="entity:JOB:1", entity_type="JOB", entity_id="1", display_name="My Job")
    return LineageResponse(nodes=[node], edges=[LineageEdge(source="a", target="b")])


@pytest.fixture(autouse=True)
def _reset_ensured():
    import backend.routes.graph_snapshots as g
    g._table_ensured = False
    yield
    g._table_ensured = False


@pytest.fixture
def mock_sql():
    with patch("backend.routes.graph_snapshots._execute_sql") as m:
        m.return_value = []
        yield m


class TestExecuteSql:
    def test_no_warehouse(self):
        import backend.routes.graph_snapshots as g
        with patch.object(g, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                g._execute_sql("SELECT 1")

    def test_success_rows(self):
        import backend.routes.graph_snapshots as g
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["v1"]]
        col = MagicMock()
        col.name = "a"
        resp.manifest.schema.columns = [col]
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            assert g._execute_sql("SELECT a") == [{"a": "v1"}]

    def test_empty_result(self):
        import backend.routes.graph_snapshots as g
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result = None
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            assert g._execute_sql("SELECT 1") == []

    def test_failed_state(self):
        import backend.routes.graph_snapshots as g
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.FAILED
        resp.status.error.message = "bad"
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            with pytest.raises(RuntimeError, match="SQL failed"):
                g._execute_sql("SELECT 1")

    def test_ensure_table_swallows_error(self):
        import backend.routes.graph_snapshots as g
        with patch.object(g, "_execute_sql", side_effect=RuntimeError("nope")):
            g._ensure_table()  # should not raise

    def test_lazy_ensure_calls_once(self):
        import backend.routes.graph_snapshots as g
        g._table_ensured = False
        with patch.object(g, "_ensure_table") as ens:
            g._lazy_ensure()
            g._lazy_ensure()
            assert ens.call_count == 1


class TestCapture:
    def test_ok(self, app_client, mock_sql):
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=_lineage_with_entity()):
            resp = app_client.post(
                "/api/snapshots/capture", json={"catalog": "c", "schema_name": "s"}
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_count"] == 1
        assert data["edge_count"] == 1
        assert data["scope"] == "c.s"

    def test_ok_no_schema(self, app_client, mock_sql):
        from backend.models import LineageResponse
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])):
            resp = app_client.post("/api/snapshots/capture", json={"catalog": "c"})
        assert resp.status_code == 200
        assert resp.json()["scope"] == "c"

    def test_missing_body_422(self, app_client, mock_sql):
        resp = app_client.post("/api/snapshots/capture", json={})
        assert resp.status_code == 422

    def test_too_large_413(self, app_client, mock_sql):
        # A single node with a >10MB id makes the serialized graph_json exceed 10MB
        from backend.models import EntityNode, LineageResponse
        big = EntityNode(id="x" * 10_000_001, entity_type="JOB", entity_id="1")
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=LineageResponse(nodes=[big], edges=[])):
            resp = app_client.post("/api/snapshots/capture", json={"catalog": "c"})
        assert resp.status_code == 413

    def test_error_500(self, app_client, mock_sql):
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   side_effect=RuntimeError("boom")):
            resp = app_client.post("/api/snapshots/capture", json={"catalog": "c"})
        assert resp.status_code == 500


class TestList:
    def test_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"snapshot_id": "s1"}]
        resp = app_client.get("/api/snapshots")
        assert resp.status_code == 200
        assert resp.json()["snapshots"]

    def test_with_scope(self, app_client, mock_sql):
        resp = app_client.get("/api/snapshots", params={"scope": "c.s"})
        assert resp.status_code == 200

    def test_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/snapshots")
        assert resp.status_code == 500


class TestGet:
    def test_found(self, app_client, mock_sql):
        mock_sql.return_value = [
            {"snapshot_id": "s1", "graph_json": '{"nodes": [], "edges": []}'}
        ]
        resp = app_client.get("/api/snapshots/s1")
        assert resp.status_code == 200
        data = resp.json()
        assert "graph" in data
        assert "graph_json" not in data

    def test_bad_json_defaults(self, app_client, mock_sql):
        mock_sql.return_value = [{"snapshot_id": "s1", "graph_json": "not-json"}]
        resp = app_client.get("/api/snapshots/s1")
        assert resp.status_code == 200
        assert resp.json()["graph"] == {"nodes": [], "edges": []}

    def test_not_found_404(self, app_client, mock_sql):
        mock_sql.return_value = []
        resp = app_client.get("/api/snapshots/missing")
        assert resp.status_code == 404

    def test_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/snapshots/s1")
        assert resp.status_code == 500


class TestDiff:
    """The HTTP /diff path is shadowed by /{snapshot_id}, so we call the
    coroutine directly to exercise diff logic."""

    def _call(self, a="a", b="b"):
        import backend.routes.graph_snapshots as g
        return asyncio.run(g.diff_snapshots(MagicMock(), a, b))

    def test_diff_added_removed(self):
        import backend.routes.graph_snapshots as g
        ga = '{"nodes": [{"id": "n1"}], "edges": [{"source": "n1", "target": "n2"}]}'
        gb = '{"nodes": [{"id": "n1"}, {"id": "n3"}], "edges": [{"source": "n1", "target": "n3"}]}'

        # Callable side_effect (never exhausts — avoids StopIteration-in-thread hang)
        def _se(sql):
            if "snapshot_id = 'a'" in sql:
                return [{"graph_json": ga}]
            if "snapshot_id = 'b'" in sql:
                return [{"graph_json": gb}]
            return []

        with patch.object(g, "_execute_sql", side_effect=_se):
            out = self._call()
        assert out["nodes_added"] == ["n3"]
        assert set(out["nodes_removed"]) == set()
        assert out["summary"]["nodes_added_count"] == 1
        assert out["summary"]["edges_added_count"] == 1
        assert out["summary"]["edges_removed_count"] == 1

    def test_diff_not_found_404(self):
        import backend.routes.graph_snapshots as g
        from fastapi import HTTPException
        with patch.object(g, "_execute_sql", side_effect=lambda sql: []):
            with pytest.raises(HTTPException) as ei:
                self._call()
        assert ei.value.status_code == 404

    def test_diff_error_500(self):
        import backend.routes.graph_snapshots as g
        from fastapi import HTTPException
        with patch.object(g, "_execute_sql", side_effect=RuntimeError("boom")):
            with pytest.raises(HTTPException) as ei:
                self._call()
        assert ei.value.status_code == 500


class TestDelete:
    """DELETE is admin-gated: a hard DELETE with no per-user scoping."""

    def test_ok(self, admin_client, mock_sql):
        resp = admin_client.delete("/api/snapshots/s1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_error_500(self, admin_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = admin_client.delete("/api/snapshots/s1")
        assert resp.status_code == 500
        # Raw SQL error text must not reach the caller.
        assert "boom" not in resp.text

    def test_non_admin_403(self, non_admin_client, mock_sql):
        resp = non_admin_client.delete("/api/snapshots/s1")
        assert resp.status_code == 403
        mock_sql.assert_not_called()

    def test_anonymous_403(self, app_client, mock_sql):
        resp = app_client.delete("/api/snapshots/s1")
        assert resp.status_code == 403
        mock_sql.assert_not_called()


class TestInjection:
    """Escaping/allow-listing of every user value that reaches SQL here."""

    # The confirmed exploit: a leading backslash makes quote-doubling alone
    # close the literal early, so the UNION runs as the app service principal.
    _UNION = ("\\' UNION SELECT email, ssn, dob, current_timestamp(), 'x', 1, 1 "
              "FROM main.pii.customers -- ")

    def test_list_scope_union_payload_400(self, app_client, mock_sql):
        resp = app_client.get("/api/snapshots", params={"scope": self._UNION})
        assert resp.status_code == 400
        mock_sql.assert_not_called()

    # An empty ?scope= means "no filter" (falsy), so it is not in this list.
    @pytest.mark.parametrize("bad", ["c'", "a.b.c", "c s", "c.", "main;drop"])
    def test_list_scope_shape_400(self, app_client, mock_sql, bad):
        resp = app_client.get("/api/snapshots", params={"scope": bad})
        assert resp.status_code == 400
        mock_sql.assert_not_called()

    @pytest.mark.parametrize("good", ["main", "main.default", "my-catalog.my_schema"])
    def test_list_scope_valid_shapes_ok(self, app_client, mock_sql, good):
        resp = app_client.get("/api/snapshots", params={"scope": good})
        assert resp.status_code == 200
        assert f"scope = '{good}'" in mock_sql.call_args[0][0]

    def test_capture_rejects_bad_scope(self, app_client, mock_sql):
        resp = app_client.post("/api/snapshots/capture",
                               json={"catalog": "c", "schema_name": "s'; DROP TABLE x; --"})
        assert resp.status_code == 400

    def test_snapshot_id_backslash_escaped(self, app_client, mock_sql):
        """snapshot_id is free-form, so it is escaped rather than allow-listed:
        the backslash is doubled FIRST, so the following '' cannot close the
        literal and the payload stays inert data."""
        mock_sql.return_value = []
        resp = app_client.get(f"/api/snapshots/{self._UNION}")
        assert resp.status_code == 404
        sql = mock_sql.call_args[0][0]
        assert self._UNION not in sql          # raw payload never emitted
        assert "\\\\''" in sql                 # backslash doubled, then quote

    def test_delete_id_backslash_escaped(self, admin_client, mock_sql):
        resp = admin_client.delete(f"/api/snapshots/{self._UNION}")
        assert resp.status_code == 200
        sql = mock_sql.call_args[0][0]
        assert self._UNION not in sql
        assert "\\\\''" in sql

    def test_diff_ids_backslash_escaped(self, mock_sql):
        # /diff is shadowed by /{snapshot_id} over HTTP, so call the coroutine.
        import backend.routes.graph_snapshots as g
        mock_sql.return_value = []
        with pytest.raises(Exception):
            asyncio.run(g.diff_snapshots(MagicMock(), self._UNION, "b"))
        selects = [c[0][0] for c in mock_sql.call_args_list if "snapshot_id =" in c[0][0]]
        assert selects and self._UNION not in selects[0]
        assert "\\\\''" in selects[0]

    def test_capture_records_real_caller(self, admin_client, mock_sql):
        """captured_by is the caller's identity, not the hardcoded 'app' —
        capture stays open to all users, so rows must be attributable."""
        from backend.models import LineageResponse
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])):
            resp = admin_client.post("/api/snapshots/capture", json={"catalog": "c"})
        assert resp.status_code == 200
        inserts = [c[0][0] for c in mock_sql.call_args_list if "INSERT INTO" in c[0][0]]
        assert inserts and "'admin@test.com'" in inserts[0]
        assert "'app'" not in inserts[0]

    def test_capture_label_backslash_escaped(self, app_client, mock_sql):
        from backend.models import LineageResponse
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])):
            resp = app_client.post("/api/snapshots/capture",
                                   json={"catalog": "c", "label": self._UNION})
        assert resp.status_code == 200
        inserts = [c[0][0] for c in mock_sql.call_args_list if "INSERT INTO" in c[0][0]]
        assert inserts and self._UNION not in inserts[0]
        assert "\\\\''" in inserts[0]

    def test_list_error_hides_sql_text(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("SQL failed: table not found")
        resp = app_client.get("/api/snapshots")
        assert resp.status_code == 500
        assert "SQL failed" not in resp.text
