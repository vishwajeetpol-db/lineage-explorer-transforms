"""Tests for backend/routes/graph_snapshots.py — versioned lineage (cap 07).

Capture / list / get / diff / delete of graph snapshots. Module-owned
`_execute_sql` is mocked.
"""
from unittest.mock import patch

import pytest


class TestSnapshots:
    def test_list_ok(self, app_client):
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.get("/api/snapshots")
        assert resp.status_code == 200

    def test_capture_ok(self, app_client):
        from backend.models import LineageResponse
        with patch("backend.routes.graph_snapshots.get_table_lineage",
                   return_value=LineageResponse(nodes=[], edges=[])), \
             patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.post("/api/snapshots/capture", json={
                "catalog": "c", "schema_name": "s"})
        assert resp.status_code in (200, 201)

    def test_get_snapshot(self, app_client):
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.get("/api/snapshots/snap123")
        assert resp.status_code in (200, 404)

    def test_diff_requires_params(self, app_client):
        # snapshot_a / snapshot_b are required, so omitting them is a 422 from
        # FastAPI's own validation. This used to be asserted as ">= 400" with the
        # comment "or 500 if the /{snapshot_id} route shadows /diff" — the
        # shadowing was real, and the loose assertion is what let it ship. Pin the
        # exact code so a re-shadowing (which yields 404 from get_snapshot) fails.
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.get("/api/snapshots/diff")
        assert resp.status_code == 422

    def test_diff_reaches_diff_handler_not_get_snapshot(self, app_client):
        """Route-order regression guard.

        `/diff` must be registered before `/{snapshot_id}`. When it was not,
        this request returned 200 with a snapshot-shaped body
        (`{"graph": {"nodes": [], "edges": []}}`), indistinguishable to a caller
        from "nothing changed" — so the whole diff feature read as empty.
        """
        with patch("backend.routes.graph_snapshots._execute_sql",
                   return_value=[{"graph_json": '{"nodes": [], "edges": []}'}]):
            resp = app_client.get("/api/snapshots/diff", params={
                "snapshot_a": "a", "snapshot_b": "b"})
        assert resp.status_code == 200
        body = resp.json()
        # the diff contract, not the snapshot contract
        for key in ("nodes_added", "nodes_removed", "edges_added",
                    "edges_removed", "summary"):
            assert key in body, f"missing {key}: got {sorted(body)}"
        assert "graph" not in body

    def test_delete_snapshot(self, admin_client):
        # DELETE is admin-gated (hard DELETE, no per-user scoping).
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = admin_client.delete("/api/snapshots/snap123")
        assert resp.status_code in (200, 204)

    def test_delete_snapshot_non_admin_403(self, non_admin_client):
        with patch("backend.routes.graph_snapshots._execute_sql") as mock_sql:
            resp = non_admin_client.delete("/api/snapshots/snap123")
        assert resp.status_code == 403
        mock_sql.assert_not_called()  # gate runs before any SQL

    def test_list_rejects_injection_scope(self, app_client):
        """The confirmed UNION exploit payload is rejected by the scope
        allow-list before it can reach SQL."""
        payload = ("\\' UNION SELECT email, ssn, dob, current_timestamp(), 'x', 1, 1 "
                   "FROM main.pii.customers -- ")
        with patch("backend.routes.graph_snapshots._execute_sql") as mock_sql:
            resp = app_client.get("/api/snapshots", params={"scope": payload})
        assert resp.status_code == 400
        mock_sql.assert_not_called()
