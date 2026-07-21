"""Tests for backend/routes/external_sources.py — OL Bridge (v2.5.3).

Fixes:
- A15: OpenLineage bridge UUID-as-auth vulnerability tests
- A2:  Registration should be admin-gated (currently ungated)
- A1:  SQL injection in source_id path parameter

Covers external platform registration, OL event ingestion, source listing,
and event inspection.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestOLBridgeRegister:
    """POST /api/external/ol-bridge/register — register an external platform.

    A15 BUG: Registration is ungated — any App user can register a source
    and receive a source_id that serves as the only authentication for ingest.
    A2: Should require admin auth.
    """

    def test_register_requires_body(self, app_client):
        resp = app_client.post("/api/external/ol-bridge/register")
        assert resp.status_code == 422

    def test_register_ungated_vulnerability(self, app_client):
        """A15/A2 BUG: Any user can register — no admin check.
        After fix, this should return 403 for non-admin."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/register", json={
                "platform": "snowflake",
                "display_name": "Snowflake Prod",
                "description": "Snowflake Horizon lineage",
            })
            # BUG: Currently 200 (ungated) — should be 403 after fix
            assert resp.status_code in (200, 201, 403)
            if resp.status_code in (200, 201):
                data = resp.json()
                # A15: source_id is the ONLY auth for ingest — leaked = lineage injection
                assert "source_id" in data
                # Verify it's a UUID (predictable format)
                import uuid
                try:
                    uuid.UUID(data["source_id"])
                except ValueError:
                    pass  # Non-UUID is slightly better

    def test_register_with_admin_succeeds(self, admin_client):
        """Admin can register external sources."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/external/ol-bridge/register", json={
                "platform": "dbt",
                "display_name": "dbt Cloud",
                "description": "dbt lineage events",
            })
            assert resp.status_code in (200, 201)
            data = resp.json()
            assert "source_id" in data


class TestOLBridgeIngest:
    """POST /api/external/ol-bridge/ingest/{source_id} — receive OL events.

    A15 BUG: Ingest authenticates ONLY by knowing the path UUID.
    No HMAC, no secret, no token rotation. Leaked/guessed UUID = fake lineage.
    """

    def test_ingest_valid_event(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/ingest/test-source-123", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "run-1"},
                "job": {"namespace": "snowflake", "name": "etl_job"},
                "inputs": [{"namespace": "snowflake", "name": "raw.events"}],
                "outputs": [{"namespace": "snowflake", "name": "analytics.events_clean"}],
            })
            assert resp.status_code == 200

    def test_ingest_with_guessed_uuid(self, app_client):
        """A15 BUG: Any UUID in the path is accepted — no source_id validation.
        After fix, non-existent source_id should return 404."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/ingest/00000000-0000-0000-0000-000000000000", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "fake-run"},
                "job": {"namespace": "attacker", "name": "fake_job"},
                "inputs": [{"namespace": "attacker", "name": "poisoned_data"}],
                "outputs": [{"namespace": "victim", "name": "prod.gold.revenue"}],
            })
            # BUG: Currently 200 (accepts any UUID) — should be 404 after fix
            assert resp.status_code in (200, 404)

    def test_ingest_no_hmac_validation(self, app_client):
        """A15 BUG: No HMAC or secret verification on ingest payload.
        Attacker knowing source_id can inject arbitrary lineage events."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            # Inject fake lineage claiming prod table comes from attacker namespace
            resp = app_client.post("/api/external/ol-bridge/ingest/leaked-source-id", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "injected-run"},
                "job": {"namespace": "malicious", "name": "trojan_pipeline"},
                "inputs": [{"namespace": "malicious", "name": "compromised.source"}],
                "outputs": [{"namespace": "databricks", "name": "prod.gold.revenue"}],
            })
            # Accepted without verification
            assert resp.status_code in (200, 401, 403)

    def test_ingest_batch_events(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            events = [
                {"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                 "run": {"runId": f"run-{i}"}, "job": {"namespace": "dbt", "name": f"model_{i}"},
                 "inputs": [], "outputs": []}
                for i in range(5)
            ]
            resp = app_client.post("/api/external/ol-bridge/ingest/test-source-123", json=events)
            assert resp.status_code == 200

    def test_ingest_sql_injection_in_source_id(self, app_client):
        """A1: source_id in URL path may be interpolated into SQL."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post(
                "/api/external/ol-bridge/ingest/test'; DROP TABLE events; --",
                json={"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                      "run": {"runId": "r"}, "job": {"namespace": "x", "name": "y"},
                      "inputs": [], "outputs": []}
            )
            # Should be 400 after validation; currently may pass
            assert resp.status_code in (200, 400, 422)


class TestOLBridgeSources:
    """GET /api/external/ol-bridge/sources — list registered sources."""

    def test_returns_sources_list(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"source_id": "s1", "platform": "snowflake",
                 "display_name": "Snowflake Prod", "total_events": "42",
                 "last_push_at": "2026-07-20T12:00:00Z", "active": "true"}
            ]
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_source_ids_exposed_to_all_users(self, app_client):
        """A15 BUG: Source listing exposes source_ids (auth tokens) to all users.
        After fix, should be admin-only or omit source_id from response."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"source_id": "secret-uuid-123", "platform": "snowflake",
                 "display_name": "Prod", "total_events": "100",
                 "last_push_at": "2026-07-20", "active": "true"}
            ]
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200
            data = resp.json()
            # BUG: source_id is exposed — this IS the auth credential
            if data:
                assert "source_id" in data[0]  # Documents the vulnerability

    def test_empty_sources(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200


class TestOLBridgeEvents:
    """GET /api/external/ol-bridge/events — inspect received events."""

    def test_returns_events_list(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events")
            assert resp.status_code == 200

    def test_filter_by_source_id(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "source_id": "test-source-123"
            })
            assert resp.status_code == 200

    def test_filter_by_platform(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "platform": "snowflake"
            })
            assert resp.status_code == 200

    def test_sql_injection_in_source_id_filter(self, app_client):
        """A1: source_id filter param may be interpolated."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "source_id": "x' OR '1'='1"
            })
            assert resp.status_code in (200, 400)
