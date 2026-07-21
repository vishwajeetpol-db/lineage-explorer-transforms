"""Shared pytest fixtures for BrickTrace backend tests."""
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
    """FastAPI TestClient for integration-style route tests."""
    from fastapi.testclient import TestClient
    # Patch workspace client before importing app
    with patch("backend.lineage_service._get_client") as mock_fn:
        mock_fn.return_value = MagicMock()
        from backend.main import app
        with TestClient(app) as client:
            yield client
