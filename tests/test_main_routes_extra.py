"""Coverage for the remaining top-level @app routes in backend/main.py.

Routes already exercised by test_routes_core_lineage.py / test_auth.py /
test_capability_cache_routes.py are NOT re-tested here. This file targets the
diagnostics, admin, transform, and control-panel routes plus the static-file
serving (logo + SPA catch-all).

All service functions are patched at ``backend.main.*`` (they are imported into
that module's namespace) or, for functions imported lazily inside a handler, at
their source module (e.g. ``backend.lineage_service.run_diagnostics``).
"""
from unittest.mock import patch

import pytest


def _freshness(exists=True, is_stale=False, age="2h ago"):
    from backend.models import FreshnessInfo
    return FreshnessInfo(exists=exists, edge_count=3, last_built="2026-07-30T10:00:00",
                         age_str=age, is_stale=is_stale)


def _diagnosis():
    from backend.models import TransformDiagnosis
    return TransformDiagnosis(reason_code="no_producer", title="t", detail="d")


def _transform_response():
    from backend.models import TransformResponse
    return TransformResponse(levels=[], has_lineage=False)


def _build_status(complete=False, success=False):
    from backend.models import BuildJobStatus
    return BuildJobStatus(run_id="123", state="RUNNING", is_complete=complete,
                          is_success=success)


# ---------------------------------------------------------------------------
# Identity / diagnostics (unauthenticated)
# ---------------------------------------------------------------------------
class TestUserInfo:
    def test_user_info_anonymous(self, app_client):
        resp = app_client.get("/api/user-info")
        assert resp.status_code == 200
        body = resp.json()
        assert body == {"email": None, "isAdmin": False}

    def test_user_info_admin(self, admin_client):
        resp = admin_client.get("/api/user-info")
        assert resp.status_code == 200
        assert resp.json()["isAdmin"] is True


class TestDiagnostics:
    def test_diagnostics_ok(self, app_client):
        with patch("backend.lineage_service.run_diagnostics",
                   return_value={"ok": True, "checks": []}):
            resp = app_client.get("/api/diagnostics")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_diagnostics_not_ok_returns_503(self, app_client):
        with patch("backend.lineage_service.run_diagnostics",
                   return_value={"ok": False, "checks": []}):
            resp = app_client.get("/api/diagnostics")
        assert resp.status_code == 503


class TestCapturePrerequisites:
    def test_prerequisites_ok(self, app_client):
        with patch("backend.edge_case_guards.check_capture_prerequisites",
                   return_value={"capture_ready": True}):
            resp = app_client.get("/api/capture/prerequisites")
        assert resp.status_code == 200
        assert resp.json()["capture_ready"] is True

    def test_prerequisites_error_500(self, app_client):
        with patch("backend.edge_case_guards.check_capture_prerequisites",
                   side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/capture/prerequisites")
        assert resp.status_code == 500
        assert resp.json()["capture_ready"] is False


class TestScdDetection:
    def test_scd_requires_fqn(self, app_client):
        resp = app_client.get("/api/scd-detection", params={"table": "just_a_name"})
        assert resp.status_code == 400

    def test_scd_missing_param_422(self, app_client):
        resp = app_client.get("/api/scd-detection")
        assert resp.status_code == 422

    def test_scd_ok(self, app_client):
        with patch("backend.edge_case_guards.detect_scd_cdc_patterns",
                   return_value={"table_fqn": "c.s.t", "is_scd": False}):
            resp = app_client.get("/api/scd-detection", params={"table": "c.s.t"})
        assert resp.status_code == 200

    def test_scd_error_500(self, app_client):
        with patch("backend.edge_case_guards.detect_scd_cdc_patterns",
                   side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/scd-detection", params={"table": "c.s.t"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Admin-gated status / cache routes
# ---------------------------------------------------------------------------
class TestAdminStatus:
    def test_status_non_admin_403(self, non_admin_client):
        resp = non_admin_client.get("/api/admin/status")
        assert resp.status_code == 403

    def test_status_admin_200(self, admin_client):
        with patch("backend.main.get_cache_snapshot", return_value=([], 0, 0)):
            resp = admin_client.get("/api/admin/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "system" in body and "cache" in body

    def test_status_admin_with_entries(self, admin_client):
        import time
        now = time.time()
        entries = [("k1", now, now, 2048), ("k2", now - 100, now - 50, 4096)]
        with patch("backend.main.get_cache_snapshot", return_value=(entries, 6144, 1)):
            resp = admin_client.get("/api/admin/status")
        assert resp.status_code == 200
        assert resp.json()["cache"]["entries"] == 2


class TestCacheInvalidate:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/cache/invalidate")
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client):
        with patch("backend.main.invalidate_cache") as mock_inv:
            resp = admin_client.post("/api/cache/invalidate")
        assert resp.status_code == 200
        mock_inv.assert_called_once()


class TestAdminEvictCache:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/admin/evict-cache", params={"key": "k"})
        assert resp.status_code == 403

    def test_missing_key_422(self, admin_client):
        resp = admin_client.post("/api/admin/evict-cache")
        assert resp.status_code == 422

    def test_admin_evicts(self, admin_client):
        with patch("backend.main.evict_cache_entry", return_value=True):
            resp = admin_client.post("/api/admin/evict-cache", params={"key": "k"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_admin_key_not_found(self, admin_client):
        with patch("backend.main.evict_cache_entry", return_value=False):
            resp = admin_client.post("/api/admin/evict-cache", params={"key": "k"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "not_found"


# ---------------------------------------------------------------------------
# Transform routes
# ---------------------------------------------------------------------------
class TestTransformFreshness:
    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/transform/freshness", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/transform/freshness",
                              params={"catalog": "c", "schema": "s", "table": "t'; DROP--"})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.main.get_transform_freshness", return_value=_freshness()):
            resp = app_client.get("/api/transform/freshness",
                                  params={"catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200
        assert resp.json()["exists"] is True

    def test_service_error_500(self, app_client):
        with patch("backend.main.get_transform_freshness", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/transform/freshness",
                                  params={"catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 500


class TestTransformDiagnose:
    def test_ok(self, app_client):
        with patch("backend.main.diagnose_missing_lineage", return_value=_diagnosis()):
            resp = app_client.get("/api/transform/diagnose",
                                  params={"catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200
        assert resp.json()["reason_code"] == "no_producer"

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/transform/diagnose",
                              params={"catalog": "c", "schema": "s", "table": "bad;"})
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.main.diagnose_missing_lineage", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/transform/diagnose",
                                  params={"catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 500


class TestTransformTrace:
    def test_ok(self, app_client):
        with patch("backend.main.backtrack_transform_lineage", return_value=_transform_response()):
            resp = app_client.get("/api/transform/trace", params={
                "catalog": "c", "schema": "s", "table": "t", "column": "col"})
        assert resp.status_code == 200
        assert "levels" in resp.json()

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/transform/trace", params={
            "catalog": "c", "schema": "s", "table": "t", "column": "c'--"})
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.main.backtrack_transform_lineage", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/transform/trace", params={
                "catalog": "c", "schema": "s", "table": "t", "column": "col"})
        assert resp.status_code == 500


class TestTransformCategories:
    def test_ok(self, app_client):
        with patch("backend.main.get_transform_categories", return_value={"CAST": "#fff"}):
            resp = app_client.get("/api/transform/categories")
        assert resp.status_code == 200
        assert resp.json()["categories"] == {"CAST": "#fff"}


class TestTransformBuildConfigured:
    def test_true(self, app_client):
        with patch("backend.main.is_build_configured", return_value=True):
            resp = app_client.get("/api/transform/build-configured")
        assert resp.status_code == 200
        assert resp.json()["configured"] is True

    def test_false(self, app_client):
        with patch("backend.main.is_build_configured", return_value=False):
            resp = app_client.get("/api/transform/build-configured")
        assert resp.json()["configured"] is False


class TestTransformStatus:
    def test_non_numeric_400(self, app_client):
        resp = app_client.get("/api/transform/status/abc")
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.main.get_build_status", return_value=_build_status()):
            resp = app_client.get("/api/transform/status/123")
        assert resp.status_code == 200

    def test_complete_invalidates_cache(self, app_client):
        with patch("backend.main.get_build_status",
                   return_value=_build_status(complete=True, success=True)), \
             patch("backend.main.invalidate_transform_cache") as mock_inv:
            resp = app_client.get("/api/transform/status/123")
        assert resp.status_code == 200
        mock_inv.assert_called_once()

    def test_error_500(self, app_client):
        with patch("backend.main.get_build_status", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/transform/status/123")
        assert resp.status_code == 500


class TestTransformInvalidate:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/transform/invalidate")
        assert resp.status_code == 403

    def test_bad_scope_400(self, admin_client):
        resp = admin_client.post("/api/transform/invalidate", params={"scope": "nope"})
        assert resp.status_code == 400

    def test_table_scope_requires_fqn(self, admin_client):
        resp = admin_client.post("/api/transform/invalidate", params={"scope": "table"})
        assert resp.status_code == 400

    def test_cache_scope_ok(self, admin_client):
        with patch("backend.main.clear_transform_lineage",
                   return_value={"scope": "cache", "cleared": []}):
            resp = admin_client.post("/api/transform/invalidate", params={"scope": "cache"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_table_scope_ok(self, admin_client):
        with patch("backend.main.clear_transform_lineage",
                   return_value={"scope": "table", "cleared": ["x"]}):
            resp = admin_client.post("/api/transform/invalidate",
                                     params={"scope": "table", "table_fqn": "c.s.t"})
        assert resp.status_code == 200

    def test_error_500(self, admin_client):
        with patch("backend.main.clear_transform_lineage", side_effect=RuntimeError("x")):
            resp = admin_client.post("/api/transform/invalidate", params={"scope": "cache"})
        assert resp.status_code == 500


class TestTransformBuild:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/transform/build",
                                     json={"table_fqn": "c.s.t"})
        assert resp.status_code == 403

    def test_not_configured_503(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=False):
            resp = admin_client.post("/api/transform/build", json={"table_fqn": "c.s.t"})
        assert resp.status_code == 503

    def test_bad_fqn_400(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=True):
            resp = admin_client.post("/api/transform/build", json={"table_fqn": "not_fqn"})
        assert resp.status_code == 400

    def test_fresh_short_circuits(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=True), \
             patch("backend.main.get_transform_freshness",
                   return_value=_freshness(exists=True, is_stale=False)):
            resp = admin_client.post("/api/transform/build", json={"table_fqn": "c.s.t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "fresh"

    def test_submitted(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=True), \
             patch("backend.main.get_transform_freshness",
                   return_value=_freshness(exists=False, is_stale=True)), \
             patch("backend.main.submit_build_job", return_value="9999"):
            resp = admin_client.post("/api/transform/build", json={"table_fqn": "c.s.t"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "submitted"
        assert resp.json()["run_id"] == "9999"

    def test_force_rebuild_skips_freshness(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=True), \
             patch("backend.main.submit_build_job", return_value="1") as mock_submit:
            resp = admin_client.post("/api/transform/build",
                                     json={"table_fqn": "c.s.t", "force_rebuild": True})
        assert resp.status_code == 200
        mock_submit.assert_called_once()

    def test_submit_error_500(self, admin_client):
        with patch("backend.main.is_build_configured", return_value=True), \
             patch("backend.main.get_transform_freshness",
                   return_value=_freshness(exists=False, is_stale=True)), \
             patch("backend.main.submit_build_job", side_effect=RuntimeError("x")):
            resp = admin_client.post("/api/transform/build", json={"table_fqn": "c.s.t"})
        assert resp.status_code == 500


class TestCapturedExpression:
    def test_ok(self, app_client):
        with patch("backend.main.get_captured_expression",
                   return_value={"expression": "a + b"}):
            resp = app_client.get("/api/transform/captured-expression", params={
                "catalog": "c", "schema": "s", "table": "t", "column": "col"})
        assert resp.status_code == 200
        assert resp.json()["captured"]["expression"] == "a + b"

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/transform/captured-expression", params={
            "catalog": "c", "schema": "s", "table": "t", "column": "c;"})
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.main.get_captured_expression", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/transform/captured-expression", params={
                "catalog": "c", "schema": "s", "table": "t", "column": "col"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Control Panel — feature flags + federated
# ---------------------------------------------------------------------------
class TestControlPanelFlags:
    def test_list_ok(self, app_client):
        with patch("backend.main.list_flags", return_value=[{"id": "f1"}]):
            resp = app_client.get("/api/control-panel/flags")
        assert resp.status_code == 200
        assert resp.json()["flags"] == [{"id": "f1"}]

    def test_list_error_500(self, app_client):
        with patch("backend.main.list_flags", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/control-panel/flags")
        assert resp.status_code == 500


class TestAccessCheck:
    def test_ok(self, app_client):
        with patch("backend.main.check_access_requirements", return_value=[{"ok": True}]):
            resp = app_client.get("/api/control-panel/access-check/some_flag")
        assert resp.status_code == 200
        assert resp.json()["flag_id"] == "some_flag"

    def test_unknown_flag_404(self, app_client):
        with patch("backend.main.check_access_requirements",
                   side_effect=ValueError("unknown flag")):
            resp = app_client.get("/api/control-panel/access-check/nope")
        assert resp.status_code == 404

    def test_error_500(self, app_client):
        with patch("backend.main.check_access_requirements", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/control-panel/access-check/f")
        assert resp.status_code == 500


class TestSetFlag:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/control-panel/flags/f1", json={"enabled": True})
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client):
        with patch("backend.main.set_flag_state", return_value={"flag_id": "f1", "enabled": True}):
            resp = admin_client.post("/api/control-panel/flags/f1", json={"enabled": True})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_unknown_flag_404(self, admin_client):
        with patch("backend.main.set_flag_state", side_effect=ValueError("unknown")):
            resp = admin_client.post("/api/control-panel/flags/nope", json={"enabled": True})
        assert resp.status_code == 404

    def test_error_500(self, admin_client):
        with patch("backend.main.set_flag_state", side_effect=RuntimeError("x")):
            resp = admin_client.post("/api/control-panel/flags/f1", json={"enabled": True})
        assert resp.status_code == 500


class TestPlanCaptureStatus:
    def test_ok(self, app_client):
        with patch("backend.main.get_plan_capture_status", return_value={"enabled": False}):
            resp = app_client.get("/api/control-panel/plan-capture/status")
        assert resp.status_code == 200

    def test_error_500(self, app_client):
        with patch("backend.main.get_plan_capture_status", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/control-panel/plan-capture/status")
        assert resp.status_code == 500


class TestFederatedStatus:
    def test_status_ok(self, app_client):
        with patch("backend.main.get_federated_sync_status", return_value={"peers": 0}):
            resp = app_client.get("/api/control-panel/federated/status")
        assert resp.status_code == 200

    def test_status_error_500(self, app_client):
        with patch("backend.main.get_federated_sync_status", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/control-panel/federated/status")
        assert resp.status_code == 500

    def test_list_peers_ok(self, app_client):
        with patch("backend.main.list_federated_peers", return_value=[{"peer_alias": "p"}]):
            resp = app_client.get("/api/control-panel/federated/peers")
        assert resp.status_code == 200
        assert resp.json()["peers"] == [{"peer_alias": "p"}]

    def test_list_peers_error_500(self, app_client):
        with patch("backend.main.list_federated_peers", side_effect=RuntimeError("x")):
            resp = app_client.get("/api/control-panel/federated/peers")
        assert resp.status_code == 500


class TestRegisterPeer:
    def test_non_admin_403(self, non_admin_client):
        resp = non_admin_client.post("/api/control-panel/federated/peers",
                                     json={"peer_alias": "p", "share_name": "s"})
        assert resp.status_code == 403

    def test_missing_fields_400(self, admin_client):
        resp = admin_client.post("/api/control-panel/federated/peers",
                                 json={"peer_alias": "", "share_name": ""})
        assert resp.status_code == 400

    def test_admin_ok(self, admin_client):
        with patch("backend.main.register_federated_peer",
                   return_value={"peer_alias": "p", "share_name": "s"}):
            resp = admin_client.post("/api/control-panel/federated/peers",
                                     json={"peer_alias": "p", "share_name": "s"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_error_500(self, admin_client):
        with patch("backend.main.register_federated_peer", side_effect=RuntimeError("x")):
            resp = admin_client.post("/api/control-panel/federated/peers",
                                     json={"peer_alias": "p", "share_name": "s"})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# Static file serving — logo + SPA catch-all
# ---------------------------------------------------------------------------
class TestStaticServing:
    def test_logo_served(self, app_client):
        resp = app_client.get("/bricktrace-logo.png")
        assert resp.status_code == 200
        # frontend/dist/bricktrace-logo.logo exists in the repo → served as PNG
        assert resp.headers["content-type"].startswith("image/png")

    def test_spa_catch_all_serves_index(self, app_client):
        resp = app_client.get("/some/unknown/route")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_spa_root(self, app_client):
        resp = app_client.get("/")
        assert resp.status_code == 200

    def test_path_traversal_falls_back_to_index(self, app_client):
        resp = app_client.get("/../../../etc/passwd")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
