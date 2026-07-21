"""Tests for backend/routes/governance.py — Governance & classification (v2.5.0).

Covers input validation, error handling, and response structure.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestGovernanceRoute:
    """GET /api/governance endpoint tests."""

    def test_missing_catalog_returns_422(self, app_client):
        """Missing required query param should return 422."""
        resp = app_client.get("/api/governance", params={"schema": "s", "table": "t"})
        assert resp.status_code == 422

    def test_missing_schema_returns_422(self, app_client):
        resp = app_client.get("/api/governance", params={"catalog": "c", "table": "t"})
        assert resp.status_code == 422

    def test_missing_table_returns_422(self, app_client):
        resp = app_client.get("/api/governance", params={"catalog": "c", "schema": "s"})
        assert resp.status_code == 422

    def test_invalid_catalog_returns_400(self, app_client):
        resp = app_client.get("/api/governance", params={
            "catalog": "bad value!", "schema": "s", "table": "t"
        })
        assert resp.status_code == 400

    def test_valid_params_succeed(self, app_client):
        """With valid params and mocked service, should return 200."""
        with patch("backend.routes.governance.get_table_governance") as mock_gov:
            mock_gov.return_value = {
                "owner": "user@test.com",
                "columns": [],
                "rules_applied": 0,
            }
            resp = app_client.get("/api/governance", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "owner" in data


class TestGovernancePropagation:
    """GET /api/governance/propagation endpoint tests."""

    def test_propagation_returns_list(self, app_client):
        with patch("backend.routes.governance.get_downstream_sensitivity_propagation") as mock_prop:
            mock_prop.return_value = []
            resp = app_client.get("/api/governance/propagation", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)


class TestGovernanceConfig:
    """GET/POST /api/governance/config endpoint tests."""

    def test_list_config_returns_list(self, app_client):
        with patch("backend.routes.governance.list_governance_rules") as mock_rules:
            mock_rules.return_value = []
            resp = app_client.get("/api/governance/config")
            assert resp.status_code == 200
            assert isinstance(resp.json(), list)

    def test_upsert_requires_body(self, app_client):
        resp = app_client.post("/api/governance/config")
        assert resp.status_code == 422
