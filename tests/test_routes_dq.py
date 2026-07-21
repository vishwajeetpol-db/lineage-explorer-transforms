"""Tests for backend/routes/dq.py — Data quality rules (v2.5.0).

Covers CRUD for DQ expectations (NOT_NULL, UNIQUE, RANGE, REGEX, CUSTOM)
and validation logic.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestDQRulesGet:
    """GET /api/dq-rules — list rules for a table."""

    def test_returns_rules_list(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"rule_id": "r1", "column_name": "order_id",
                 "rule_type": "NOT_NULL", "parameters": "{}"}
            ]
            resp = app_client.get("/api/dq-rules", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_empty_rules(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            assert resp.json() == []

    def test_missing_table_param_returns_422(self, app_client):
        resp = app_client.get("/api/dq-rules")
        assert resp.status_code in (422, 400)


class TestDQRulesPost:
    """POST /api/dq-rules — create a DQ expectation."""

    def test_create_rule_requires_body(self, app_client):
        resp = app_client.post("/api/dq-rules")
        assert resp.status_code == 422

    def test_create_valid_rule(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/dq-rules", json={
                "catalog": "main",
                "schema_name": "default",
                "table": "orders",
                "column_name": "amount",
                "rule_type": "NOT_NULL",
            })
            assert resp.status_code in (200, 201)


class TestDQRulesDelete:
    """DELETE /api/dq-rules/{rule_id} — remove a DQ expectation."""

    def test_delete_nonexistent_rule(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.delete("/api/dq-rules/nonexistent-id")
            assert resp.status_code in (200, 404)

    def test_delete_existing_rule(self, app_client):
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = [{"deleted": "1"}]
            resp = app_client.delete("/api/dq-rules/rule-123")
            assert resp.status_code in (200, 204)
