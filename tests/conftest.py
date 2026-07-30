"""Shared pytest fixtures for BrickTrace backend tests.

Covers:
- A9:  Correct API contract testing (wrapped responses, proper params)
- A14: LOCAL_DEV_ADMIN_EMAIL privilege escalation testing
- A2:  Admin-gated vs ungated route testing
- A1:  SQL injection vector testing via crafted inputs
- C9:  Multi-user App SP visibility testing
"""
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

# Ensure backend package is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Set default test env vars before any backend imports
os.environ.setdefault("DATABRICKS_WAREHOUSE_ID", "test-warehouse-id")
os.environ.setdefault("LINEAGE_CATALOG", "test_catalog")
os.environ.setdefault("LINEAGE_SCHEMA", "test_schema")
os.environ.setdefault("ADMIN_GROUP_NAME", "admins")
# Ensure LOCAL_DEV_ADMIN_EMAIL is NOT set by default (A14 security)
os.environ.pop("LOCAL_DEV_ADMIN_EMAIL", None)


@pytest.fixture(autouse=True)
def _reset_global_state():
    """Reset module-level singletons/caches between tests.

    The FastAPI `app` and several module globals are process-wide singletons
    shared by every TestClient. Without a reset, state leaks across test files:
      * the RateLimitMiddleware's per-user request buckets accumulate — after
        RATE_LIMIT_MAX_REQUESTS (60) anonymous requests every later test gets a
        spurious 429 (this was the main cause of order-dependent failures);
      * a stale `_user_info_cache` entry can make requests resolve to the wrong
        identity.
    Clearing these keeps each test hermetic regardless of file order.
    Best-effort: any missing attribute is ignored.
    """
    def _reset():
        # Rate-limiter request buckets on the (lazily built) middleware stack.
        try:
            import backend.main as _m
            node = getattr(_m.app, "middleware_stack", None)
            while node is not None:
                reqs = getattr(node, "requests", None)
                if isinstance(reqs, dict):
                    reqs.clear()
                node = getattr(node, "app", None)
        except Exception:
            pass
        # User-info auth cache.
        try:
            import backend.main as _m
            if hasattr(_m, "_user_info_cache"):
                _m._user_info_cache.clear()
        except Exception:
            pass
        # Lineage LRU + cost globals — leaking these makes later tests' fetch
        # paths serve from cache (not execute), deflating that module's coverage.
        try:
            import backend.lineage_service as _ls
            _ls.invalidate_cache()
            for attr in ("_cost_by_job_id", "_cost_by_pipeline_id"):
                d = getattr(_ls, attr, None)
                if isinstance(d, dict):
                    d.clear()
            if hasattr(_ls, "_cost_cache_fetched_at"):
                _ls._cost_cache_fetched_at = 0.0
        except Exception:
            pass

    _reset()   # before the test
    yield
    _reset()   # and after


@pytest.fixture
def mock_workspace_client():
    """Mock WorkspaceClient for all tests that need SDK access."""
    with patch("backend.lineage_service._get_client") as mock_fn:
        client = MagicMock()
        mock_fn.return_value = client
        yield client


@pytest.fixture
def mock_execute_sql():
    """Patch _execute_sql at module level for routes that define it locally."""
    with patch("backend.routes.impact._execute_sql") as mock_fn:
        mock_fn.return_value = []
        yield mock_fn


@pytest.fixture
def mock_feature_flags_sql():
    """Patch SQL execution for feature flags module."""
    with patch("backend.feature_flags._execute_sql") as mock_fn:
        mock_fn.return_value = []
        yield mock_fn


@pytest.fixture
def app_client():
    """FastAPI TestClient for integration-style route tests (unauthenticated).

    No x-forwarded-access-token header — simulates anonymous App access.
    Without LOCAL_DEV_ADMIN_EMAIL, user is (None, False) = non-admin.
    """
    from fastapi.testclient import TestClient
    with patch("backend.lineage_service._get_client") as mock_fn:
        mock_fn.return_value = MagicMock()
        from backend.main import app
        with TestClient(app) as client:
            yield client


@pytest.fixture
def admin_client():
    """FastAPI TestClient with admin identity (A2: admin-gate tests).

    Mocks _get_user_info to return an admin user so admin-gated routes
    can be tested for correct behavior when authorized.
    """
    from fastapi.testclient import TestClient
    with patch("backend.lineage_service._get_client") as mock_fn:
        mock_fn.return_value = MagicMock()
        with patch("backend.main._get_user_info", return_value=("admin@test.com", True)):
            from backend.main import app
            with TestClient(app) as client:
                yield client


@pytest.fixture
def non_admin_client():
    """FastAPI TestClient with non-admin identity (A2: admin-gate tests).

    Mocks _get_user_info to return a regular user so admin-gated routes
    can be tested to confirm they reject non-admins with 403.
    """
    from fastapi.testclient import TestClient
    with patch("backend.lineage_service._get_client") as mock_fn:
        mock_fn.return_value = MagicMock()
        with patch("backend.main._get_user_info", return_value=("user@test.com", False)):
            from backend.main import app
            with TestClient(app) as client:
                yield client


@pytest.fixture
def local_dev_admin_client():
    """FastAPI TestClient with LOCAL_DEV_ADMIN_EMAIL set (A14 vuln test).

    Simulates the dangerous condition where LOCAL_DEV_ADMIN_EMAIL is
    accidentally set on a deployed App, granting admin to everyone.
    """
    from fastapi.testclient import TestClient
    with patch.dict(os.environ, {"LOCAL_DEV_ADMIN_EMAIL": "dev@test.com"}):
        with patch("backend.lineage_service._get_client") as mock_fn:
            mock_fn.return_value = MagicMock()
            from backend.main import app
            with TestClient(app) as client:
                yield client
