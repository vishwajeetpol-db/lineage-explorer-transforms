"""Tests for backend/routes/observability.py — Run health (v2.5.0).

Fixes:
- A10: Silent empty 200s masking grant/SQL failures
- C8:  Warehouse stopped / SQL timeout scenarios
- C1:  System tables disabled / SP grants missing

The `/api/observability` endpoint takes entity_type (JOB|PIPELINE) + entity_id
and delegates to backend.server.observability.get_entity_health. The
catalog/schema/table shape lives on `/api/observability/producers`, which
delegates to get_table_producer_health. Service errors are wrapped as HTTP 500
(no longer swallowed into an empty 200).
"""
from unittest.mock import patch, MagicMock

import pytest


class TestEntityHealthRoute:
    """GET /api/observability endpoint tests (entity_type + entity_id)."""

    def test_missing_params_returns_422(self, app_client):
        resp = app_client.get("/api/observability")
        assert resp.status_code in (422, 400)

    def test_invalid_entity_type_returns_400(self, app_client):
        """entity_type must be JOB or PIPELINE."""
        resp = app_client.get("/api/observability", params={
            "entity_type": "DROP TABLE;", "entity_id": "my_job"
        })
        assert resp.status_code == 400

    def test_valid_request_returns_health_data(self, app_client):
        with patch("backend.routes.observability.get_entity_health") as mock_health:
            mock_health.return_value = {
                "entity_type": "PIPELINE", "entity_id": "abc-123",
                "total_runs": 10, "successful_runs": 8, "failed_runs": 2,
                "last_run_at": "2026-07-20T10:00:00Z",
            }
            resp = app_client.get("/api/observability", params={
                "entity_type": "PIPELINE", "entity_id": "abc-123"
            })
            assert resp.status_code == 200

    def test_no_runs_returns_empty_health(self, app_client):
        with patch("backend.routes.observability.get_entity_health") as mock_health:
            mock_health.return_value = {}
            resp = app_client.get("/api/observability", params={
                "entity_type": "JOB", "entity_id": "some_job"
            })
            assert resp.status_code == 200

    def test_sql_failure_surfaces_500(self, app_client):
        """A10: SQL failure is no longer swallowed into an empty 200 — the
        route wraps the service error as HTTP 500 so the grant/table failure
        is surfaced rather than masked."""
        with patch("backend.routes.observability.get_entity_health") as mock_health:
            mock_health.side_effect = RuntimeError(
                "SQL failed: TABLE_OR_VIEW_NOT_FOUND: system.access.audit"
            )
            resp = app_client.get("/api/observability", params={
                "entity_type": "JOB", "entity_id": "some_job"
            })
            assert resp.status_code == 500

    def test_warehouse_timeout_surfaces_500(self, app_client):
        """C8: Warehouse stopped / SQL timeout surfaces as 500, not empty 200."""
        with patch("backend.routes.observability.get_entity_health") as mock_health:
            mock_health.side_effect = RuntimeError("SQL failed: WAREHOUSE_TIMEOUT")
            resp = app_client.get("/api/observability", params={
                "entity_type": "PIPELINE", "entity_id": "abc-123"
            })
            assert resp.status_code == 500

    def test_system_tables_disabled_surfaces_500(self, app_client):
        """C1: System tables disabled at account level — surfaced as 500."""
        with patch("backend.routes.observability.get_entity_health") as mock_health:
            mock_health.side_effect = RuntimeError(
                "INSUFFICIENT_PERMISSIONS: system tables not enabled"
            )
            resp = app_client.get("/api/observability", params={
                "entity_type": "JOB", "entity_id": "some_job"
            })
            assert resp.status_code == 500

    def test_sql_injection_in_entity_id(self, app_client):
        """A1: Injection in entity_id is rejected by the entity_id validator."""
        resp = app_client.get("/api/observability", params={
            "entity_type": "JOB", "entity_id": "job'; DROP TABLE--"
        })
        assert resp.status_code == 400


class TestProducerHealthRoute:
    """GET /api/observability/producers endpoint tests (catalog/schema/table)."""

    def test_missing_params_returns_422(self, app_client):
        resp = app_client.get("/api/observability/producers", params={
            "schema": "s", "table": "t"
        })
        assert resp.status_code == 422

    def test_valid_request_returns_producers(self, app_client):
        with patch("backend.routes.observability.get_table_producer_health") as mock_ph:
            mock_ph.return_value = [
                {"entity_type": "PIPELINE", "entity_id": "abc", "total_runs": 3}
            ]
            resp = app_client.get("/api/observability/producers", params={
                "catalog": "main", "schema": "default", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            # Producers list is wrapped under a "producers" key.
            assert isinstance(data, dict)
            assert isinstance(data["producers"], list)

    def test_sql_injection_in_catalog(self, app_client):
        """A1: Injection in catalog is rejected by the identifier validator."""
        resp = app_client.get("/api/observability/producers", params={
            "catalog": "main' UNION SELECT--", "schema": "default", "table": "orders"
        })
        assert resp.status_code == 400

    def test_sql_injection_in_schema(self, app_client):
        resp = app_client.get("/api/observability/producers", params={
            "catalog": "main", "schema": "default' OR '1'='1", "table": "orders"
        })
        assert resp.status_code == 400
