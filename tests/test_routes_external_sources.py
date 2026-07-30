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

    def test_register_now_admin_gated(self, app_client):
        """A15/A2 FIX: Registration is now admin-gated — a non-admin request
        is rejected with 403 (a leaked source_id = lineage injection, so the
        trust anchor must be admin-only)."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post("/api/external/ol-bridge/register", json={
                "platform": "snowflake",
                "name": "Snowflake Prod",
                "description": "Snowflake Horizon lineage",
            })
            assert resp.status_code == 403

    def test_register_with_admin_succeeds(self, admin_client):
        """Admin can register external sources."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/external/ol-bridge/register", json={
                "platform": "dbt",
                "name": "dbt Cloud",
                "description": "dbt lineage events",
            })
            assert resp.status_code in (200, 201)
            data = resp.json()
            # A15: source_id is the ONLY auth for ingest — leaked = lineage injection.
            assert "source_id" in data
            # Verify it's a UUID (predictable format — documents the weakness).
            import uuid
            uuid.UUID(data["source_id"])


class TestOLBridgeIngest:
    """POST /api/external/ol-bridge/ingest/{source_id} — receive OL events.

    A15 BUG: Ingest authenticates ONLY by knowing the path UUID.
    No HMAC, no secret, no token rotation. Leaked/guessed UUID = fake lineage.
    """

    # A valid UUID that also passes the ingest route's _UUID_RE check.
    _VALID_UUID = "12345678-1234-1234-1234-123456789abc"

    def test_ingest_valid_event(self, app_client):
        """Ingest succeeds only for a well-formed UUID that maps to a known,
        active source. Mock the source-existence SELECT to return a row."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            # First meaningful call is the source lookup — return an active row
            # so ingest proceeds (CREATE/INSERT/UPDATE ignore the return value).
            mock_sql.return_value = [{"platform": "snowflake", "name": "Snowflake Prod"}]
            resp = app_client.post(f"/api/external/ol-bridge/ingest/{self._VALID_UUID}", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "run-1"},
                "job": {"namespace": "snowflake", "name": "etl_job"},
                "inputs": [{"namespace": "snowflake", "name": "raw.events"}],
                "outputs": [{"namespace": "snowflake", "name": "analytics.events_clean"}],
            })
            assert resp.status_code == 200

    def test_ingest_with_guessed_uuid_rejected(self, app_client):
        """A15 FIX: A well-formed but unregistered UUID is rejected. The source
        lookup returns no row, so ingest fails with 403 (not 200) — a guessed
        UUID can no longer inject lineage. The 403 (not 404) also avoids
        confirming whether the UUID exists (enumeration protection)."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []  # source not found
            resp = app_client.post("/api/external/ol-bridge/ingest/00000000-0000-0000-0000-000000000000", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "fake-run"},
                "job": {"namespace": "attacker", "name": "fake_job"},
                "inputs": [{"namespace": "attacker", "name": "poisoned_data"}],
                "outputs": [{"namespace": "victim", "name": "prod.gold.revenue"}],
            })
            assert resp.status_code == 403

    def test_ingest_no_hmac_still_only_uuid_auth(self, app_client):
        """A15: Ingest auth is still ONLY the path UUID — there is no HMAC or
        payload signature. A caller that KNOWS a valid, registered source_id can
        inject arbitrary lineage. Documents the remaining weakness: with a
        known-good source row, the malicious payload is accepted (200)."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [{"platform": "databricks", "name": "leaked"}]
            resp = app_client.post(f"/api/external/ol-bridge/ingest/{self._VALID_UUID}", json={
                "eventType": "COMPLETE",
                "eventTime": "2026-07-20T12:00:00Z",
                "run": {"runId": "injected-run"},
                "job": {"namespace": "malicious", "name": "trojan_pipeline"},
                "inputs": [{"namespace": "malicious", "name": "compromised.source"}],
                "outputs": [{"namespace": "databricks", "name": "prod.gold.revenue"}],
            })
            # No HMAC check — a known source_id is sufficient to inject.
            assert resp.status_code == 200

    def test_ingest_batch_events(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [{"platform": "dbt", "name": "dbt Cloud"}]
            events = {
                "events": [
                    {"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                     "run": {"runId": f"run-{i}"}, "job": {"namespace": "dbt", "name": f"model_{i}"},
                     "inputs": [], "outputs": []}
                    for i in range(5)
                ]
            }
            resp = app_client.post(f"/api/external/ol-bridge/ingest/{self._VALID_UUID}", json=events)
            assert resp.status_code == 200

    def test_ingest_sql_injection_in_source_id(self, app_client):
        """A1/A15: A source_id that isn't a well-formed UUID (including an
        injection payload) is rejected up front by _UUID_RE with 400 — it never
        reaches SQL interpolation."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post(
                "/api/external/ol-bridge/ingest/test'; DROP TABLE events; --",
                json={"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                      "run": {"runId": "r"}, "job": {"namespace": "x", "name": "y"},
                      "inputs": [], "outputs": []}
            )
            assert resp.status_code in (400, 422)


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
            # Sources are wrapped under a "sources" key with a "count".
            assert isinstance(data, dict)
            assert isinstance(data["sources"], list)

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
            sources = data["sources"]
            # BUG: source_id is exposed — this IS the auth credential
            if sources:
                assert "source_id" in sources[0]  # Documents the vulnerability

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
