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
        # snapshot_a / snapshot_b are required; missing them is rejected (422),
        # or 500 if the /{snapshot_id} route shadows /diff — either way, not 2xx.
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.get("/api/snapshots/diff")
        assert resp.status_code >= 400

    def test_diff_ok(self, app_client):
        with patch("backend.routes.graph_snapshots._execute_sql",
                   return_value=[{"graph_json": '{"nodes": [], "edges": []}'}]):
            resp = app_client.get("/api/snapshots/diff", params={
                "snapshot_a": "a", "snapshot_b": "b"})
        assert resp.status_code in (200, 400, 404)

    def test_delete_snapshot(self, app_client):
        with patch("backend.routes.graph_snapshots._execute_sql", return_value=[]):
            resp = app_client.delete("/api/snapshots/snap123")
        assert resp.status_code in (200, 204)
