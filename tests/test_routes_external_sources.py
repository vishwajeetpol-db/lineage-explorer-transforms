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
    """GET /api/external/ol-bridge/sources — list registered sources.

    Admin-gated: the rows carry source_id, which doubles as the ingest
    credential, so listing them to everyone hands out the injection token.
    """

    def test_returns_sources_list(self, admin_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"source_id": "s1", "platform": "snowflake",
                 "display_name": "Snowflake Prod", "total_events": "42",
                 "last_push_at": "2026-07-20T12:00:00Z", "active": "true"}
            ]
            resp = admin_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 200
            data = resp.json()
            # Sources are wrapped under a "sources" key with a "count".
            assert isinstance(data, dict)
            assert isinstance(data["sources"], list)

    def test_source_ids_admin_only(self, non_admin_client):
        """A15 FIX: source_id is the ingest credential, so the listing that
        discloses it is now admin-only — a non-admin gets 403 and no SQL runs."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            resp = non_admin_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 403
            mock_sql.assert_not_called()

    def test_anonymous_sources_403(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            resp = app_client.get("/api/external/ol-bridge/sources")
            assert resp.status_code == 403
            mock_sql.assert_not_called()

    def test_empty_sources(self, admin_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.get("/api/external/ol-bridge/sources")
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
        """A1: source_id filter param is interpolated, so it must be escaped
        backslash-first — quote-doubling alone is bypassable."""
        payload = "\\' UNION SELECT event_id, source_id, platform, job_namespace, " \
                  "job_name, event_type, event_time, received_at FROM secrets -- "
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "source_id": payload
            })
            assert resp.status_code == 200
            sql = mock_sql.call_args[0][0]
            assert payload not in sql      # raw payload never reaches SQL
            assert "\\\\''" in sql         # backslash doubled before the quote

    def test_platform_filter_allow_listed(self, app_client):
        """platform is enum-like (see _ALLOWED_PLATFORMS), so an off-list value
        — including the UNION payload — is rejected with 400, never escaped."""
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "platform": "\\' UNION SELECT * FROM secrets -- "
            })
            assert resp.status_code == 400
            mock_sql.assert_not_called()

    def test_platform_filter_case_insensitive(self, app_client):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/external/ol-bridge/events", params={
                "platform": "Snowflake"
            })
            assert resp.status_code == 200
            assert "platform = 'snowflake'" in mock_sql.call_args[0][0]
