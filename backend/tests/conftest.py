"""
Shared test fixtures.

Tests do NOT talk to a real DBSQL warehouse — `_execute_sql` is monkey-patched
to return canned rows. That keeps the suite fast (~1s) and hermetic so CI
doesn't depend on BP workspace reachability.
"""
import os
import sys
import types
import pytest

# Ensure an env var is set so lineage_service doesn't error on import-time checks.
os.environ.setdefault("DATABRICKS_WAREHOUSE_ID", "test-warehouse-id")

# `databricks-sdk` pulls in Google/OAuth deps that aren't needed for tests.
# We only import the SDK for its type hints and WorkspaceClient — a stub
# is enough to let the test suite import lineage_service in environments
# where the full SDK isn't available (slim CI runners, sandboxes).
if "databricks.sdk" not in sys.modules:
    try:
        import databricks.sdk  # noqa: F401
    except ImportError:
        # Create a stub that satisfies import-time references.
        pkg = types.ModuleType("databricks")
        pkg.__path__ = []
        sys.modules["databricks"] = pkg
        sdk = types.ModuleType("databricks.sdk")
        sdk.WorkspaceClient = type("WorkspaceClient", (), {})
        sys.modules["databricks.sdk"] = sdk
        svc = types.ModuleType("databricks.sdk.service")
        sys.modules["databricks.sdk.service"] = svc
        sql = types.ModuleType("databricks.sdk.service.sql")
        class _StatementState:
            SUCCEEDED = "SUCCEEDED"
            FAILED = "FAILED"
        sql.StatementState = _StatementState
        sys.modules["databricks.sdk.service.sql"] = sql


@pytest.fixture(autouse=True)
def clear_cache():
    """Each test starts with an empty cache so we don't leak state between cases."""
    from backend import lineage_service
    lineage_service.invalidate_cache()
    yield
    lineage_service.invalidate_cache()


@pytest.fixture
def mock_execute_sql(monkeypatch):
    """Replace `_execute_sql` with a dispatcher keyed on SQL substring.

    Usage:
        def test_x(mock_execute_sql):
            mock_execute_sql.register("SHOW CATALOGS", [{"catalog": "main"}])
            ...
    """
    from backend import lineage_service

    class _Dispatcher:
        def __init__(self):
            self._rules: list[tuple[str, list[dict]]] = []
            self.calls: list[str] = []

        def register(self, sql_substring: str, rows: list[dict]):
            self._rules.append((sql_substring, rows))

        def __call__(self, client, sql, catalog=None):
            self.calls.append(sql)
            for pattern, rows in self._rules:
                if pattern in sql:
                    return rows
            return []

    dispatcher = _Dispatcher()
    monkeypatch.setattr(lineage_service, "_execute_sql", dispatcher)
    # Also stub the workspace client so `_get_client` doesn't try to authenticate.
    monkeypatch.setattr(lineage_service, "_get_client", lambda: object())
    return dispatcher
