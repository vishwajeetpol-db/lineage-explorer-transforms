"""Coverage-focused tests for backend/routes/diagnostics.py (caps 31-36).

Drives root-cause tracing, SCD/CDC spec + targets, schema-change detection,
column profiling (incl. admin-gated live=true), capability billing, and the
admin-gated federated verify/sync endpoints. Service functions are mocked at
their import location. No backend/ edits; everything mocked; fast + offline.
"""
from unittest.mock import patch

import pytest


class TestRootCause:
    def test_ok(self, app_client):
        with patch("backend.routes.diagnostics.trace_root_cause", return_value={"candidates": []}):
            resp = app_client.post(
                "/api/diagnostics/root-cause",
                json={"catalog": "c", "schema_name": "s", "table": "t", "column": "col"},
            )
        assert resp.status_code == 200
        assert "candidates" in resp.json()

    def test_missing_body_422(self, app_client):
        resp = app_client.post("/api/diagnostics/root-cause", json={"catalog": "c"})
        assert resp.status_code == 422

    def test_bad_identifier_400(self, app_client):
        resp = app_client.post(
            "/api/diagnostics/root-cause",
            json={"catalog": "c;x", "schema_name": "s", "table": "t", "column": "col"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.diagnostics.trace_root_cause", side_effect=RuntimeError("boom")):
            resp = app_client.post(
                "/api/diagnostics/root-cause",
                json={"catalog": "c", "schema_name": "s", "table": "t", "column": "col"},
            )
        assert resp.status_code == 500


class TestScd:
    def test_found(self, app_client):
        with patch("backend.routes.diagnostics.get_cdc_spec", return_value={"scd_type": 2}):
            resp = app_client.get(
                "/api/diagnostics/scd",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 200
        assert resp.json()["found"] is True

    def test_not_found(self, app_client):
        with patch("backend.routes.diagnostics.get_cdc_spec", return_value=None):
            resp = app_client.get(
                "/api/diagnostics/scd",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 200
        assert resp.json()["found"] is False

    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/diagnostics/scd", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_bad_identifier_400(self, app_client):
        resp = app_client.get(
            "/api/diagnostics/scd",
            params={"catalog": "c;x", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.diagnostics.get_cdc_spec", side_effect=RuntimeError("boom")):
            resp = app_client.get(
                "/api/diagnostics/scd",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 500


class TestScdTargets:
    def test_ok(self, app_client):
        with patch("backend.routes.diagnostics.list_cdc_targets", return_value=[{"table": "c.s.t"}]):
            resp = app_client.get("/api/diagnostics/scd/targets")
        assert resp.status_code == 200
        assert resp.json()["targets"]

    def test_error_500(self, app_client):
        with patch("backend.routes.diagnostics.list_cdc_targets", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/diagnostics/scd/targets")
        assert resp.status_code == 500


class TestSchemaChanges:
    def test_single_table(self, app_client):
        with patch("backend.routes.diagnostics.detect_breaking_changes",
                   return_value={"breaking": []}):
            resp = app_client.get(
                "/api/diagnostics/schema-changes",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 200
        assert "breaking" in resp.json()

    def test_catalog_scan(self, app_client):
        with patch("backend.routes.diagnostics.detect_schema_changes_for_catalog",
                   return_value=[{"table": "c.s.t"}]):
            resp = app_client.get(
                "/api/diagnostics/schema-changes",
                params={"catalog": "c", "schema": "s"},
            )
        assert resp.status_code == 200
        assert resp.json()["tables_with_breaking_changes"] == 1

    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/diagnostics/schema-changes", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_bad_identifier_400(self, app_client):
        resp = app_client.get(
            "/api/diagnostics/schema-changes",
            params={"catalog": "c;x", "schema": "s"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.diagnostics.detect_schema_changes_for_catalog",
                   side_effect=RuntimeError("boom")):
            resp = app_client.get(
                "/api/diagnostics/schema-changes",
                params={"catalog": "c", "schema": "s"},
            )
        assert resp.status_code == 500


class TestProfile:
    def test_ok(self, app_client):
        with patch("backend.routes.diagnostics.get_column_profile", return_value={"columns": []}):
            resp = app_client.get(
                "/api/diagnostics/profile",
                params={"catalog": "c", "schema": "s", "table": "t", "columns": "a,b"},
            )
        assert resp.status_code == 200

    def test_bad_column_400(self, app_client):
        resp = app_client.get(
            "/api/diagnostics/profile",
            params={"catalog": "c", "schema": "s", "table": "t", "columns": "a,b;x"},
        )
        assert resp.status_code == 400

    def test_live_non_admin_403(self, non_admin_client):
        resp = non_admin_client.get(
            "/api/diagnostics/profile",
            params={"catalog": "c", "schema": "s", "table": "t", "live": "true"},
        )
        assert resp.status_code == 403

    def test_live_admin_ok(self, admin_client):
        with patch("backend.routes.diagnostics.get_column_profile", return_value={"columns": []}):
            resp = admin_client.get(
                "/api/diagnostics/profile",
                params={"catalog": "c", "schema": "s", "table": "t", "live": "true"},
            )
        assert resp.status_code == 200

    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/diagnostics/profile", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_error_500(self, app_client):
        with patch("backend.routes.diagnostics.get_column_profile", side_effect=RuntimeError("boom")):
            resp = app_client.get(
                "/api/diagnostics/profile",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 500


class TestBilling:
    def test_ok(self, app_client):
        with patch("backend.feature_flags.get_capability_live_billing",
                   return_value={"cost_usd": 1.0}):
            resp = app_client.get("/api/diagnostics/billing/cap33")
        assert resp.status_code == 200
        assert resp.json()["cost_usd"] == 1.0

    def test_error_500(self, app_client):
        with patch("backend.feature_flags.get_capability_live_billing",
                   side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/diagnostics/billing/cap33")
        assert resp.status_code == 500


class TestFederatedVerify:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.get("/api/diagnostics/federated/verify/peer1")
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client):
        with patch("backend.federated_sync.verify_peer_trust", return_value={"trusted": True}):
            resp = admin_client.get("/api/diagnostics/federated/verify/peer1")
        assert resp.status_code == 200
        assert resp.json()["trusted"] is True

    def test_admin_error_500(self, admin_client):
        with patch("backend.federated_sync.verify_peer_trust", side_effect=RuntimeError("boom")):
            resp = admin_client.get("/api/diagnostics/federated/verify/peer1")
        assert resp.status_code == 500


class TestFederatedSync:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/diagnostics/federated/sync/peer1")
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client):
        with patch("backend.federated_sync.trigger_peer_sync_job", return_value={"run_id": "r1"}):
            resp = admin_client.post("/api/diagnostics/federated/sync/peer1")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == "r1"

    def test_admin_error_500(self, admin_client):
        with patch("backend.federated_sync.trigger_peer_sync_job", side_effect=RuntimeError("boom")):
            resp = admin_client.post("/api/diagnostics/federated/sync/peer1")
        assert resp.status_code == 500
