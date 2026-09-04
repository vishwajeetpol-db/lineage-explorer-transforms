"""Tests for backend.server.access (capability 20 — access & security lineage).

Covers _execute_sql (incl. the poll-past-wait-cap loop), declared grants,
audit access, identity metadata, recent events, the single audit scan, the
combined summary (reads/writes/dormant-grant detection), and schema-level
access — happy path, empty rows, and exception fallbacks.
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import access


def _col(name):
    c = MagicMock()
    c.name = name
    return c


def _resp(state=StatementState.SUCCEEDED, columns=None, data=None, error=None, statement_id="s1"):
    resp = MagicMock()
    resp.status.state = state
    resp.status.error = error
    resp.statement_id = statement_id
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        resp.manifest.schema.columns = [_col(c) for c in (columns or [])]
    return resp


class TestExecuteSql:
    def test_no_warehouse_raises(self):
        with patch.object(access, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                access._execute_sql("SELECT 1")

    def test_success_returns_dicts(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a", "b"], data=[[1, 2], [3, 4]]
        )
        with patch.object(access, "WAREHOUSE_ID", "wh"), \
             patch.object(access, "_get_client", return_value=client):
            rows = access._execute_sql("SELECT a, b")
        assert rows == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def test_empty_result_returns_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(access, "WAREHOUSE_ID", "wh"), \
             patch.object(access, "_get_client", return_value=client):
            assert access._execute_sql("SELECT 1") == []

    def test_failed_state_raises(self):
        err = MagicMock()
        err.message = "boom"
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=err
        )
        with patch.object(access, "WAREHOUSE_ID", "wh"), \
             patch.object(access, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="boom"):
                access._execute_sql("SELECT 1")

    def test_polls_pending_then_succeeds(self):
        client = MagicMock()
        pending = _resp(state=StatementState.PENDING)
        done = _resp(columns=["x"], data=[[9]])
        client.statement_execution.execute_statement.return_value = pending
        client.statement_execution.get_statement.return_value = done
        with patch.object(access, "WAREHOUSE_ID", "wh"), \
             patch.object(access, "_get_client", return_value=client), \
             patch.object(access.time, "sleep"):
            rows = access._execute_sql("SELECT x")
        assert rows == [{"x": 9}]

    def test_poll_budget_exceeded_raises(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.RUNNING
        )
        client.statement_execution.get_statement.return_value = _resp(
            state=StatementState.RUNNING
        )
        with patch.object(access, "WAREHOUSE_ID", "wh"), \
             patch.object(access, "_get_client", return_value=client), \
             patch.object(access.time, "sleep"), \
             patch.object(access.time, "time", side_effect=[1000.0, 5000.0]):
            with pytest.raises(RuntimeError, match="budget"):
                access._execute_sql("SELECT x")


class TestDeclaredGrants:
    def test_collapses_privileges_per_grantee(self):
        rows = [
            {"grantor": "admin", "grantee": "alice", "privilege_type": "SELECT", "is_grantable": "NO"},
            {"grantor": "admin", "grantee": "alice", "privilege_type": "MODIFY", "is_grantable": "NO"},
            {"grantor": "admin", "grantee": "alice", "privilege_type": "SELECT", "is_grantable": "NO"},
            {"grantor": None, "grantee": None, "privilege_type": "SELECT", "is_grantable": "NO"},
        ]
        with patch.object(access, "_execute_sql", return_value=rows):
            out = access.get_declared_grants("c", "s", "t")
        assert len(out) == 1
        assert out[0]["principal"] == "alice"
        assert out[0]["privileges"] == ["SELECT", "MODIFY"]

    def test_exception_returns_empty(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError("x")):
            assert access.get_declared_grants("c", "s", "t") == []


class TestAuditAccess:
    def test_maps_rows(self):
        rows = [{"user_email": "a@x.com", "action_name": "getTable",
                 "access_count": "5", "last_accessed_at": "2026-01-01"}]
        with patch.object(access, "_execute_sql", return_value=rows):
            out = access.get_audit_access("c", "s", "t")
        assert out[0]["access_count"] == 5
        assert out[0]["last_accessed_at"] == "2026-01-01"

    def test_exception_returns_empty(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError):
            assert access.get_audit_access("c", "s", "t") == []


class TestTableIdentities:
    def test_populated(self):
        rows = [{"table_owner": "o", "created": "2020", "created_by": "cb",
                 "last_altered": "2021", "last_altered_by": "lb"}]
        with patch.object(access, "_execute_sql", return_value=rows):
            out = access.get_table_identities("c", "s", "t")
        assert out["owner"] == "o"
        assert out["created_at"] == "2020"
        assert out["last_altered_by"] == "lb"

    def test_empty_rows_returns_defaults(self):
        with patch.object(access, "_execute_sql", return_value=[]):
            out = access.get_table_identities("c", "s", "t")
        assert out["owner"] is None

    def test_exception_returns_defaults(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError):
            out = access.get_table_identities("c", "s", "t")
        assert out["owner"] is None


class TestRecentEvents:
    def test_maps_rows(self):
        rows = [{"event_time": "t", "action_name": "getTable",
                 "user_email": "u", "source_ip_address": "1.2.3.4"}]
        with patch.object(access, "_execute_sql", return_value=rows):
            out = access.get_recent_events("c", "s", "t", limit=5)
        assert out[0]["source_ip"] == "1.2.3.4"

    def test_exception_returns_empty(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError):
            assert access.get_recent_events("c", "s", "t") == []


class TestScanAudit:
    def test_happy(self):
        rows = [{"event_time": "t", "user_email": "u", "action_name": "getTable", "source_ip": "ip"}]
        with patch.object(access, "_execute_sql", return_value=rows):
            assert access._scan_audit("c", "s", "t") == rows

    def test_exception_returns_empty(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError):
            assert access._scan_audit("c", "s", "t") == []


class TestAccessSummary:
    def test_rollup_reads_writes_and_dormant(self):
        events = [
            {"event_time": "2026-01-03", "user_email": "alice", "action_name": "getTable"},
            {"event_time": "2026-01-02", "user_email": "alice", "action_name": "getTable"},
            {"event_time": "2026-01-01", "user_email": "carol", "action_name": "updateTable"},
        ]
        grants = [
            {"principal": "alice", "privileges": ["SELECT"]},
            {"principal": "bob", "privilege": "ALL PRIVILEGES"},   # dormant (never in audit)
            {"principal": "readerless", "privileges": ["MODIFY"]},  # cannot read
        ]
        with patch.object(access, "get_declared_grants", return_value=grants), \
             patch.object(access, "get_table_identities", return_value={"owner": "o"}), \
             patch.object(access, "_scan_audit", return_value=events):
            out = access.get_access_summary("c", "s", "t")
        assert out["read_count"] == 2
        assert out["write_count"] == 1
        assert out["dormant_grants"] == ["bob"]      # SELECT-capable but never accessed
        assert out["unique_empirical_users"] == 2
        assert out["grantee_count"] == 3
        assert len(out["audit_access"]) == 2         # (alice,getTable) + (carol,updateTable)

    def test_empty_events(self):
        with patch.object(access, "get_declared_grants", return_value=[]), \
             patch.object(access, "get_table_identities", return_value={}), \
             patch.object(access, "_scan_audit", return_value=[]):
            out = access.get_access_summary("c", "s", "t")
        assert out["read_count"] == 0
        assert out["dormant_grants"] == []
        assert out["recent_events"] == []


class TestSchemaAccessSummary:
    def test_happy(self):
        rows = [{"table_full_name": "c.s.t", "unique_users": 3, "total_accesses": 10}]
        with patch.object(access, "_execute_sql", return_value=rows):
            assert access.get_schema_access_summary("c", "s") == rows

    def test_exception_returns_empty(self):
        with patch.object(access, "_execute_sql", side_effect=RuntimeError):
            assert access.get_schema_access_summary("c", "s") == []
