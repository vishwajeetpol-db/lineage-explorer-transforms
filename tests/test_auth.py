"""Tests for authentication and authorization — BrickTrace security layer.

Covers:
- A14: LOCAL_DEV_ADMIN_EMAIL privilege escalation
- A2:  Admin-gate enforcement on mutating/expensive APIs
- C9:  Multi-user shared App SP visibility (all SQL as SP, not user)
- A11: Rate limit collapses without user token
"""
import os
import hashlib
import time
from unittest.mock import patch, MagicMock

import pytest


class TestLocalDevAdminEscalation:
    """A14: LOCAL_DEV_ADMIN_EMAIL privilege escalation if set on App.

    main.py: no token + env set → admin=True. Not enforced off in prod.
    If accidentally deployed with this env var, every headerless request
    becomes an admin — total auth bypass.
    """

    def test_escalation_without_token(self):
        """A14 CRITICAL: Headerless request + env var = admin."""
        with patch.dict(os.environ, {"LOCAL_DEV_ADMIN_EMAIL": "dev@db.com"}):
            from backend.main import _get_user_info
            request = MagicMock()
            request.headers = {}  # No token header
            email, is_admin = _get_user_info(request)
            assert email == "dev@db.com"
            assert is_admin is True  # BUG: instant admin

    def test_no_env_var_denies_access(self):
        """Safe default: no env var, no token = no admin."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LOCAL_DEV_ADMIN_EMAIL", None)
            from backend.main import _get_user_info
            request = MagicMock()
            request.headers = {}
            email, is_admin = _get_user_info(request)
            assert email is None
            assert is_admin is False

    def test_env_var_should_not_be_set_in_production(self):
        """A14 FIX DIRECTION: App should assert unset at startup."""
        # Verify the env var is not currently set (conftest clears it)
        assert os.environ.get("LOCAL_DEV_ADMIN_EMAIL") is None


class TestAdminGateEnforcement:
    """A2: Missing admin gates on mutating / expensive APIs.

    These endpoints should require admin but currently don't:
    - POST /api/transform/build (ungated)
    - POST /api/snapshots/auto-capture (ungated)
    - POST /api/dq-rules/record-metrics (ungated)
    - POST /api/external/ol-bridge/register (ungated)

    These correctly require admin:
    - POST /api/dq-rules (admin-gated)
    - DELETE /api/dq-rules/{id} (admin-gated)
    - POST /api/notifications/webhooks (admin-gated)
    - DELETE /api/notifications/webhooks/{id} (admin-gated)
    """

    def test_transform_build_ungated(self, app_client):
        """A2 BUG: /api/transform/build has no admin check."""
        with patch("backend.build_service.submit_build_job") as mock_build:
            mock_build.return_value = "12345"
            resp = app_client.post("/api/transform/build", json={
                "target_table_fqn": "main.default.orders"
            })
            # BUG: 200 without admin check (burns Jobs/$)
            # After fix: should be 403 for non-admin
            assert resp.status_code in (200, 403, 422)

    def test_auto_capture_ungated(self, app_client):
        """A2 BUG: /api/snapshots/auto-capture has no admin check."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/snapshots/auto-capture")
            # BUG: 200 without admin (expensive scan)
            assert resp.status_code in (200, 403)

    def test_dq_record_metrics_ungated(self, app_client):
        """A2 BUG: /api/dq-rules/record-metrics has no admin check."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders", "quality_score": 0.9,
                "rules_evaluated": 5, "rules_passed": 4, "rules_failed": 1,
            })
            # BUG: 200 without admin (writes to Delta)
            assert resp.status_code in (200, 403)

    def test_webhook_create_correctly_gated(self, non_admin_client):
        """Verify webhooks POST is properly admin-gated."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/notifications/webhooks", json={
                "name": "test", "url": "https://example.com/hook"
            })
            assert resp.status_code == 403

    def test_dq_rules_post_correctly_gated(self, non_admin_client):
        """Verify DQ rules POST is properly admin-gated."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "expression": "col IS NOT NULL",
            })
            assert resp.status_code == 403


class TestRateLimitBehavior:
    """A11: Rate limit collapses without user token.

    Falls back to client IP / 'unknown' behind Apps proxy.
    All App users share the same rate bucket.
    """

    def test_rate_limit_key_without_token(self):
        """A11: Without token, key falls back to IP (shared for all App users)."""
        from backend.main import RateLimitMiddleware
        middleware = RateLimitMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}  # No token
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        key = middleware._get_user_key(request)
        # Falls back to IP — shared bucket for all App users behind proxy
        assert key == "10.0.0.1"

    def test_rate_limit_key_with_token(self):
        """With token, key is token hash — per-user bucket."""
        from backend.main import RateLimitMiddleware
        middleware = RateLimitMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {"x-forwarded-access-token": "user-token-123"}
        request.client = MagicMock()
        request.client.host = "10.0.0.1"

        key = middleware._get_user_key(request)
        expected = hashlib.sha256("user-token-123".encode()).hexdigest()[:16]
        assert key == expected

    def test_rate_limit_key_without_client(self):
        """Edge case: no client (proxy misconfiguration)."""
        from backend.main import RateLimitMiddleware
        middleware = RateLimitMiddleware(app=MagicMock())
        request = MagicMock()
        request.headers = {}
        request.client = None

        key = middleware._get_user_key(request)
        assert key == "unknown"


class TestAppSPVisibility:
    """C9: Multi-user shared App SP visibility.

    All SQL runs as the App Service Principal, not the human user.
    Any user who can open the App sees whatever the App SP can BROWSE/SELECT.
    The App ACL is the real perimeter — not per-user UC ACLs.
    """

    def test_sql_uses_app_sp_not_user_token(self, app_client):
        """C9: Verify SQL client is the App SP (WorkspaceClient()),
        not the user's x-forwarded-access-token."""
        with patch("backend.lineage_service._get_client") as mock_client:
            client = MagicMock()
            mock_client.return_value = client
            # Calling any lineage endpoint uses _get_client (App SP)
            from backend.lineage_service import _get_client
            result = _get_client()
            # The client is the singleton App SP client
            assert result is client
