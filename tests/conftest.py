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
