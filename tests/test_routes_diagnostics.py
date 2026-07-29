"""Tests for backend/routes/diagnostics.py — diagnostics suite (caps 31-36).

Root-cause, SCD/CDC spec viewer, schema-change detector, column profiling,
per-flag billing, and federated peer verify/sync (admin-gated).
"""
from unittest.mock import patch

import pytest


class TestDiagnosticsRootCause:
    def test_injection_400(self, app_client):
        resp = app_client.post("/api/diagnostics/root-cause", json={
            "catalog": "bad;", "schema_name": "s", "table": "t", "column": "x"})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.routes.diagnostics.trace_root_cause", return_value={"candidates": []}):
            resp = app_client.post("/api/diagnostics/root-cause", json={
                "catalog": "c", "schema_name": "s", "table": "t", "column": "x"})
        assert resp.status_code == 200


class TestScd:
    def test_scd_requires_params(self, app_client):
        resp = app_client.get("/api/diagnostics/scd", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_scd_injection_400(self, app_client):
        resp = app_client.get("/api/diagnostics/scd", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400

    def test_scd_targets_ok(self, app_client):
        with patch("backend.routes.diagnostics.list_cdc_targets", return_value=[]):
            resp = app_client.get("/api/diagnostics/scd/targets")
        assert resp.status_code == 200


class TestSchemaChanges:
    def test_requires_params(self, app_client):
        resp = app_client.get("/api/diagnostics/schema-changes", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/diagnostics/schema-changes", params={
            "catalog": "bad;", "schema": "s"})
        assert resp.status_code == 400


class TestColumnProfile:
    def test_requires_params(self, app_client):
        resp = app_client.get("/api/diagnostics/profile", params={"catalog": "c"})
        assert resp.status_code == 422


class TestFederatedDiagnostics:
    def test_verify_requires_admin(self, non_admin_client):
        resp = non_admin_client.get("/api/diagnostics/federated/verify/peer1")
        assert resp.status_code == 403

    def test_sync_requires_admin(self, non_admin_client):
        resp = non_admin_client.post("/api/diagnostics/federated/sync/peer1")
        assert resp.status_code == 403
