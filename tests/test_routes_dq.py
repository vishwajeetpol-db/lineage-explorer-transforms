"""Tests for backend/routes/dq.py — Data quality rules (v2.5.0).

Fixes:
- A9:  Corrected API contracts (table_fqn param, {rules:[]} wrapper, DQRuleIn model)
- A1:  SQL injection tests for CUSTOM/RANGE expression field
- A2:  Admin-gate tests for POST and DELETE (require admin)
- C10: DQ profiling without catalog SELECT failure scenario

Covers CRUD for DQ expectations (NOT_NULL, UNIQUE, RANGE, REGEX, CUSTOM)
and validation logic.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestDQRulesGet:
    """GET /api/dq-rules — list rules for a table.

    A9 fix: Param is `table_fqn` (three-part, optional), NOT catalog/schema/table.
    Response is {"rules": [...]} not a bare list.
    """

    def test_returns_rules_wrapped_dict(self, app_client):
        """A9: Response is {rules: [...]} not a bare list."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"rule_id": "r1", "column_name": "order_id",
                 "rule_type": "NOT_NULL", "expression": "order_id IS NOT NULL",
                 "table_fqn": "main.default.orders"}
            ]
            resp = app_client.get("/api/dq-rules", params={
                "table_fqn": "main.default.orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            # A9: Must be a dict with "rules" key, not a bare list
            assert isinstance(data, dict)
            assert "rules" in data
            assert isinstance(data["rules"], list)

    def test_no_filter_returns_all_rules(self, app_client):
        """A9: table_fqn is Optional — omitting returns all rules."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules")
            # table_fqn is Optional, should NOT return 422
            assert resp.status_code == 200
            data = resp.json()
            assert "rules" in data

    def test_invalid_table_fqn_returns_400(self, app_client):
        """Invalid three-part name should be rejected."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules", params={
                "table_fqn": "invalid format"
            })
            assert resp.status_code == 400

    def test_sql_injection_in_table_fqn(self, app_client):
        """A1: table_fqn is validated by _FULL_NAME_RE — injection blocked."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules", params={
                "table_fqn": "main.default.orders'; DROP TABLE dq_rules; --"
            })
            assert resp.status_code == 400


class TestDQRulesColumns:
    """GET /api/dq-rules/columns — column-level rules for a table."""

    def test_requires_all_params(self, app_client):
        """catalog, schema, table are all required."""
        resp = app_client.get("/api/dq-rules/columns")
        assert resp.status_code == 422

    def test_valid_params_return_columns(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"column_name": "order_id", "rule_type": "NOT_NULL",
                 "expression": "order_id IS NOT NULL", "severity": "ERROR"}
            ]
            resp = app_client.get("/api/dq-rules/columns", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "table_fqn" in data
            assert "columns" in data

    def test_rejects_invalid_identifiers(self, app_client):
        """A1: Validates catalog/schema/table identifiers."""
        resp = app_client.get("/api/dq-rules/columns", params={
            "catalog": "main;DROP", "schema": "default", "table": "orders"
        })
        assert resp.status_code == 400


class TestDQRulesPost:
    """POST /api/dq-rules — create a DQ expectation.

    A2 fix: Requires admin auth (403 for non-admin).
    A9 fix: Body requires DQRuleIn model (table_fqn, expression required).
    """

    def test_create_rule_rejects_non_admin(self, non_admin_client):
        """A2: POST requires admin — non-admin gets 403."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "NOT_NULL",
                "expression": "amount IS NOT NULL",
            })
            assert resp.status_code == 403

    def test_create_rule_requires_body(self, admin_client):
        """Missing body returns 422 (Pydantic validation)."""
        resp = admin_client.post("/api/dq-rules")
        assert resp.status_code == 422

    def test_create_valid_rule_admin(self, admin_client):
        """A9: Correct DQRuleIn body with table_fqn and expression."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "NOT_NULL",
                "expression": "amount IS NOT NULL",
                "severity": "ERROR",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "rule_id" in data
            assert data["status"] == "upserted"

    def test_invalid_table_fqn_returns_400(self, admin_client):
        """Invalid table_fqn format rejected."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "invalid",
                "expression": "col IS NOT NULL",
            })
            assert resp.status_code == 400

    def test_sql_injection_via_custom_expression(self, admin_client):
        """A1 FIX: CUSTOM expressions are checked against an allow-list grammar,
        so a query keyword is rejected at write time (400)."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "CUSTOM",
                "expression": "1=1 UNION SELECT * FROM information_schema.tables--",
                "severity": "ERROR",
            })
            assert resp.status_code == 400

    def test_scalar_subquery_expression_rejected(self, admin_client):
        """A1 FIX: the read-oracle payload. A bare scalar subquery needs no quotes
        and tripped none of the old deny-list patterns; the allow-list rejects it,
        so it can never be stored and later executed by /metrics."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "rule_type": "CUSTOM",
                "expression": "(SELECT count(*) FROM main.hr.payroll WHERE salary > 250000) > 0",
            })
            assert resp.status_code == 400
            assert "SELECT" in resp.json()["detail"]

    def test_legitimate_custom_expression_accepted(self, admin_client):
        """A1 FIX (other direction): an ordinary rule with string literals and an
        IN list must still be storable."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "CUSTOM",
                "expression": "amount > 0 AND status IN ('active','pending')",
            })
            assert resp.status_code == 200

    def test_sql_injection_via_notes_field(self, admin_client):
        """A1: notes field is also interpolated into INSERT."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "expression": "col IS NOT NULL",
                "notes": "test'); DROP TABLE dq_rules; --",
            })
            assert resp.status_code in (200, 400)


class TestDQRulesDelete:
    """DELETE /api/dq-rules/{rule_id} — remove a DQ expectation.

    A2 fix: Requires admin auth (403 for non-admin).
    """

    def test_delete_rejects_non_admin(self, non_admin_client):
        """A2: DELETE requires admin."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.delete("/api/dq-rules/rule-123")
            assert resp.status_code == 403

    def test_delete_admin_success(self, admin_client):
        """A2: Admin can delete rules."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.delete("/api/dq-rules/rule123")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "deleted"

    def test_delete_sql_injection_in_rule_id(self, admin_client):
        """A1 FIX: rule_id is truncated then escaped with sql_str, so the quotes
        stay inside the literal instead of being silently deleted."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.delete("/api/dq-rules/x' OR '1'='1")
            assert resp.status_code == 200
            sent = mock_sql.call_args[0][0]
            assert "'x'' OR ''1''=''1'" in sent

    def test_delete_backslash_quote_rule_id_cannot_close_literal(self, admin_client):
        r"""A1 FIX: a `\'`-prefixed value. Quote-doubling alone produced `\''`,
        whose second quote closes the literal on Databricks SQL; sql_str escapes
        the backslash first so both quotes stay inside."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.delete("/api/dq-rules/\\' OR 1=1--")
            assert resp.status_code == 200
            sent = mock_sql.call_args[0][0]
            assert "'\\\\'' OR 1=1--'" in sent
            assert "'\\''" not in sent  # the bypassable form must not appear


class TestDQLiveMetrics:
    """GET /api/dq-rules/metrics — execute rules live.

    C10: Requires catalog SELECT (breaks metadata-only isolation).
    A2 FIX: now admin-gated — it executes stored CUSTOM expressions as the app SP.
    """

    def test_rejects_non_admin(self, non_admin_client):
        """A2 FIX: executing stored SQL requires admin."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.get("/api/dq-rules/metrics", params={
                "table_fqn": "main.default.orders"
            })
            assert resp.status_code == 403
            # Gate runs before any SQL — nothing is executed for a non-admin.
            mock_sql.assert_not_called()

    def test_requires_table_fqn(self, admin_client):
        resp = admin_client.get("/api/dq-rules/metrics")
        assert resp.status_code == 422

    def test_invalid_table_fqn_rejected(self, admin_client):
        resp = admin_client.get("/api/dq-rules/metrics", params={
            "table_fqn": "bad;injection"
        })
        assert resp.status_code == 400

    def test_valid_request_succeeds(self, admin_client):
        """C10: Live metrics require SELECT on target table (data access)."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.get("/api/dq-rules/metrics", params={
                "table_fqn": "main.default.orders"
            })
            assert resp.status_code == 200
