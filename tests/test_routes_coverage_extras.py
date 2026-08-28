"""Coverage for previously-untested endpoints across several routers:
- /api/access + /api/access/schema
- /api/observability/producers
- ML extensions (/api/ml feature-tables, vector-indexes, vector-lineage,
  prompt-lineage, inference-tables)
- /api/governance/config DELETE (admin-gated)
- /api/dq-rules/propagation

Service layers mocked; no live workspace needed.
"""
from unittest.mock import patch

import pytest


class TestAccessRoutes:
    """These routes are admin-gated: they report which NAMED people read a table.

    See the module docstring in backend/routes/access.py for why gating beats
    redaction here.
    """

    def test_requires_params(self, admin_client):
        resp = admin_client.get("/api/access", params={"catalog": "c", "schema": "s"})
        assert resp.status_code == 422

    def test_injection_400(self, admin_client):
        resp = admin_client.get("/api/access", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400

    def test_ok(self, admin_client):
        with patch("backend.routes.access.get_access_summary",
                   return_value={"table_full_name": "c.s.t", "identities": {},
                                 "declared_grants": [], "audit_access": []}):
            resp = admin_client.get("/api/access", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200

    def test_schema_access_ok(self, admin_client):
        with patch("backend.routes.access.get_schema_access_summary", return_value=[]):
            resp = admin_client.get("/api/access/schema", params={"catalog": "c", "schema": "s"})
        assert resp.status_code == 200
        assert "tables" in resp.json()

    def test_non_admin_gets_403(self, non_admin_client):
        """The gate, and the fact that it precedes the audit query entirely."""
        with patch("backend.routes.access.get_access_summary") as spy:
            resp = non_admin_client.get("/api/access", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 403
        spy.assert_not_called()

    def test_non_admin_gets_403_on_schema_route(self, non_admin_client):
        with patch("backend.routes.access.get_schema_access_summary") as spy:
            resp = non_admin_client.get("/api/access/schema", params={"catalog": "c", "schema": "s"})
        assert resp.status_code == 403
        spy.assert_not_called()

    def test_gate_precedes_validation(self, non_admin_client):
        """403, not 400: an unauthorized caller learns nothing about the payload."""
        resp = non_admin_client.get("/api/access", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 403


class TestObservabilityProducers:
    def test_requires_params(self, app_client):
        resp = app_client.get("/api/observability/producers", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_ok(self, app_client):
        with patch("backend.routes.observability.get_table_producer_health", return_value=[]):
            resp = app_client.get("/api/observability/producers", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200
        assert "producers" in resp.json()


class TestMlExtensions:
    def test_feature_tables_ok(self, app_client):
        with patch("backend.routes.ml._execute_sql", return_value=[]):
            resp = app_client.get("/api/ml/feature-tables")
        assert resp.status_code == 200
        assert "feature_tables" in resp.json()

    def test_vector_indexes_ok(self, app_client):
        with patch("backend.routes.ml._execute_sql", return_value=[]):
            resp = app_client.get("/api/ml/vector-indexes")
        assert resp.status_code == 200
        assert "vector_indexes" in resp.json()

    def test_vector_indexes_degrades_gracefully(self, app_client):
        """Vector-search system tables may not exist — should not 500."""
        with patch("backend.routes.ml._execute_sql", side_effect=RuntimeError("no such table")):
            resp = app_client.get("/api/ml/vector-indexes")
        assert resp.status_code == 200
        assert "note" in resp.json()

    def test_vector_lineage_requires_index(self, app_client):
        resp = app_client.get("/api/ml/vector-lineage")
        assert resp.status_code == 422

    def test_prompt_lineage_requires_endpoint(self, app_client):
        resp = app_client.get("/api/ml/prompt-lineage")
        assert resp.status_code == 422

    def test_inference_tables_ok(self, app_client):
        with patch("backend.routes.ml._execute_sql", return_value=[]):
            resp = app_client.get("/api/ml/inference-tables")
        assert resp.status_code == 200


class TestGovernanceDelete:
    def test_delete_requires_admin(self, non_admin_client):
        resp = non_admin_client.delete("/api/governance/config", params={"rule_id": "r1"})
        assert resp.status_code == 403

    def test_delete_admin_ok(self, admin_client):
        with patch("backend.routes.governance.delete_governance_rule",
                   return_value={"rule_id": "r1", "status": "deleted"}):
            resp = admin_client.delete("/api/governance/config", params={"rule_id": "r1"})
        assert resp.status_code == 200


class TestDqPropagation:
    def test_propagation_requires_params(self, app_client):
        resp = app_client.get("/api/dq-rules/propagation", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_propagation_ok(self, app_client):
        with patch("backend.routes.dq._execute_sql", return_value=[]):
            resp = app_client.get("/api/dq-rules/propagation", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200
