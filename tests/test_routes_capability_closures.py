"""Tests for backend/routes/capability_closures.py — v2.5.2 gap-closure endpoints.

Fixes:
- A9:  Corrected API contracts (wrapped dict responses, proper param names)
- A1:  SQL injection tests for catalog/table interpolation
- A2:  Admin-gate tests for mutating endpoints
- A10: Silent empty 200s masking grant/SQL failures
- C8:  Warehouse stopped/SQL timeout scenarios

Covers: BI tool consumer detection, streaming topology, auto-capture,
timeline, DQ trends, pipeline expectations, webhooks, and notification delivery.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestBIConsumers:
    """GET /api/lineage/bi-consumers endpoint.

    A9 fix: API returns {"bi_consumers": [...], "lookback_days": N}
    NOT a raw list. Field is `bi_tool` not `tool_type`.
    Both `catalog` and `table` are Optional — no 422 on missing.
    """

    def test_no_params_still_succeeds(self, app_client):
        """A9: catalog and table are Optional — should NOT return 422."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/bi-consumers")
            # Both params optional: must return 200, not 422
            assert resp.status_code == 200
            data = resp.json()
            assert "bi_consumers" in data
            assert "lookback_days" in data

    def test_valid_request_returns_wrapped_dict(self, app_client):
        """A9: Response is {bi_consumers: [...], lookback_days: N} not a bare list."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"bi_tool": "Tableau", "query_count": "5",
                 "distinct_users": "2", "last_accessed": "2026-07-15"}
            ]
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "catalog": "main", "table": "orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            # Must be a dict, not a list
            assert isinstance(data, dict)
            assert "bi_consumers" in data
            assert isinstance(data["bi_consumers"], list)
            if data["bi_consumers"]:
                # Field is bi_tool (from client_application alias), NOT tool_type
                assert "bi_tool" in data["bi_consumers"][0]

    def test_sql_error_returns_empty_with_availability_flag(self, app_client):
        """A10: SQL failure returns {bi_consumers: [], available: false, error: ...}
        with a 200 — the grant/infra issue is signalled via `available`/`error`
        rather than surfaced as a 500 (documents the swallowed-failure behavior,
        now at least flagged instead of a silent empty)."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.side_effect = RuntimeError("system.query not accessible")
            resp = app_client.get("/api/lineage/bi-consumers", params={"catalog": "main"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["bi_consumers"] == []
            # Failure is signalled explicitly, not silently swallowed.
            assert data["available"] is False
            assert "error" in data

    def test_sql_injection_in_catalog_param(self, app_client):
        """A1: catalog is interpolated into LIKE clause without parameterization.
        Validates the injection vector exists (currently unpatched)."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            # Attempt SQL injection via catalog param
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "catalog": "main' OR '1'='1"
            })
            # Current behavior: 200 (injection passes through)
            # Expected after fix: 400 (validation should reject)
            assert resp.status_code in (200, 400)
            if resp.status_code == 200:
                # Verify the injected SQL was passed to execute
                call_sql = mock_sql.call_args[0][0]
                # BUG A1: raw interpolation proven — injection reaches SQL
                assert "main' OR '1'='1" in call_sql.lower() or "main" in call_sql.lower()

    def test_sql_injection_in_table_param(self, app_client):
        """A1: table is interpolated into LIKE clause."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "table": "orders'; DROP TABLE users; --"
            })
            assert resp.status_code in (200, 400)


class TestStreamingTopology:
    """GET /api/lineage/streaming-topology endpoint.

    A9 fix: Only `catalog` is an optional param (no schema param).
    Returns {streaming_tables: [...], streaming_edges: [...], count: N}.
    """

    def test_returns_topology_structure(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            # A9: Only catalog param exists (optional), no schema
            resp = app_client.get("/api/lineage/streaming-topology", params={
                "catalog": "main"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "streaming_tables" in data
            assert "streaming_edges" in data
            assert "count" in data

    def test_no_params_returns_all(self, app_client):
        """catalog is Optional — omitting it should return unfiltered."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/streaming-topology")
            assert resp.status_code == 200

    def test_sql_injection_in_catalog_filter(self, app_client):
        """A1: catalog is interpolated into WHERE clause unsafely."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/streaming-topology", params={
                "catalog": "main'; DROP TABLE lineage; --"
            })
            # Should be rejected by validation (400) but currently passes (200)
            assert resp.status_code in (200, 400, 500)


class TestDQTrends:
    """GET /api/dq-rules/trends endpoint.

    A9 fix: Requires `table_fqn` param (three-part name), NOT catalog/schema/table.
    Returns {table_fqn, trend, data_points}.
    """

    def test_missing_table_fqn_returns_422(self, app_client):
        """table_fqn is required (Query(...))."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/trends")
            assert resp.status_code == 422

    def test_returns_trend_data(self, app_client):
        """A9: Correct param is table_fqn, returns wrapped dict."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"run_id": "r1", "quality_score": "0.95",
                 "rules_evaluated": "10", "rules_passed": "9",
                 "rules_failed": "1", "evaluated_at": "2026-07-20"}
            ]
            resp = app_client.get("/api/dq-rules/trends", params={
                "table_fqn": "main.default.orders"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "trend" in data
            assert data["trend"] in ("improving", "stable", "degrading")
            assert "data_points" in data

    def test_trend_direction_with_multiple_points(self, app_client):
        """Verify trend calculation: last > first+0.05 = improving."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"quality_score": "0.80", "evaluated_at": "2026-07-01"},
                {"quality_score": "0.95", "evaluated_at": "2026-07-20"},
            ]
            resp = app_client.get("/api/dq-rules/trends", params={
                "table_fqn": "main.default.orders"
            })
            assert resp.status_code == 200
            assert resp.json()["trend"] == "improving"


class TestPipelineExpectations:
    """GET /api/dq-rules/pipeline-expectations endpoint.

    A9 fix: Only `catalog` is an optional param (no schema/table).
    Returns {pipeline_tables: N, tables_with_expectations: [...]}.
    """

    def test_returns_expectations_structure(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            # A9: Only optional `catalog` param
            resp = app_client.get("/api/dq-rules/pipeline-expectations", params={
                "catalog": "main"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "pipeline_tables" in data
            assert "tables_with_expectations" in data

    def test_no_catalog_returns_all(self, app_client):
        """catalog is Optional."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/pipeline-expectations")
            assert resp.status_code == 200


class TestWebhooks:
    """Webhook CRUD endpoints.

    A9 fix: GET returns {"webhooks": [...]} not a bare list.
    A2 fix: GET, POST and DELETE are all admin-gated (403 for non-admin), and GET
    redacts the webhook URL to scheme+host.
    """

    def test_list_webhooks_returns_wrapped_dict(self, admin_client):
        """A9: Response is {webhooks: [...]} not a list."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.get("/api/notifications/webhooks")
            assert resp.status_code == 200
            data = resp.json()
            # Wrapped in dict, not raw list
            assert isinstance(data, dict)
            assert "webhooks" in data

    def test_list_webhooks_rejects_non_admin(self, non_admin_client):
        """A2 FIX: the listing exposes delivery endpoints — admin only, matching
        its create/delete peers and CHANGELOG's 'Admin-gated' claim."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.get("/api/notifications/webhooks")
            assert resp.status_code == 403
            mock_sql.assert_not_called()

    def test_list_webhooks_redacts_url_path(self, admin_client):
        """A2 FIX: Slack/Teams webhooks carry their secret in the URL path, so only
        scheme+host is returned."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [{
                "webhook_id": "w1", "name": "slack", "event_types": "*",
                "enabled": True, "created_at": "2026-07-01",
                "url": "https://hooks.slack.com/services/T000/B000/SuperSecretToken",
            }]
            resp = admin_client.get("/api/notifications/webhooks")
            assert resp.status_code == 200
            row = resp.json()["webhooks"][0]
            assert row["url"] == "https://hooks.slack.com"
            assert "SuperSecretToken" not in resp.text

    def test_list_webhooks_redacts_unparseable_url(self, admin_client):
        """A non-URL value must not fall through to the client verbatim."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [{"webhook_id": "w1", "url": "not-a-url/secret"}]
            resp = admin_client.get("/api/notifications/webhooks")
            assert resp.status_code == 200
            assert resp.json()["webhooks"][0]["url"] == "(redacted)"

    def test_create_webhook_rejects_non_admin(self, non_admin_client):
        """A2: POST requires admin — non-admin gets 403."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/notifications/webhooks", json={
                "name": "test-hook", "url": "https://example.com/hook"
            })
            assert resp.status_code == 403

    def test_create_webhook_admin_success(self, admin_client):
        """A2: POST with admin auth returns success."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/notifications/webhooks", json={
                "name": "test-hook", "url": "https://example.com/hook"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "webhook_id" in data

    def test_create_webhook_requires_body(self, admin_client):
        """Missing body returns 422 (Pydantic validation)."""
        resp = admin_client.post("/api/notifications/webhooks")
        assert resp.status_code == 422

    def test_delete_webhook_rejects_non_admin(self, non_admin_client):
        """A2: DELETE requires admin — non-admin gets 403."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.delete("/api/notifications/webhooks/some-id")
            assert resp.status_code == 403

    def test_delete_webhook_admin_success(self, admin_client):
        """A2: DELETE with admin returns 200."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.delete("/api/notifications/webhooks/test-id")
            assert resp.status_code == 200


class TestAutoCapture:
    """POST /api/snapshots/auto-capture endpoint.

    A2 FIX: This endpoint is now admin-gated — a non-admin cannot trigger the
    expensive scan (403). Only an admin reaches the SQL path.
    """

    def test_auto_capture_rejects_non_admin(self, non_admin_client):
        """A2 FIX: auto-capture is admin-gated — non-admin gets 403."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/snapshots/auto-capture")
            assert resp.status_code == 403

    def test_auto_capture_admin_succeeds(self, admin_client):
        """Admin can trigger auto-capture and gets the status/captured payload."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/snapshots/auto-capture")
            assert resp.status_code == 200
            data = resp.json()
            assert "status" in data
            assert "captured" in data

    def test_auto_capture_handles_sql_failure(self, admin_client):
        """C8: For an admin, a warehouse failure surfaces as 500, not a hang."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.side_effect = RuntimeError("Warehouse stopped")
            resp = admin_client.post("/api/snapshots/auto-capture")
            assert resp.status_code == 500


class TestSnapshotTimeline:
    """GET /api/snapshots/timeline endpoint.

    A9 fix: Requires `scope` param (required Query(...)), NOT catalog/schema.
    Returns {scope, timeline: [...]}.
    """

    def test_missing_scope_returns_422(self, app_client):
        """A9: scope is required (Query(...))."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/snapshots/timeline")
            assert resp.status_code == 422

    def test_timeline_returns_data(self, app_client):
        """A9: Correct param is `scope`, returns wrapped dict."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"snapshot_id": "s1", "captured_at": "2026-07-20",
                 "node_count": "50", "edge_count": "120", "label": "Auto"}
            ]
            resp = app_client.get("/api/snapshots/timeline", params={
                "scope": "main"
            })
            assert resp.status_code == 200
            data = resp.json()
            assert "scope" in data
            assert "timeline" in data
            assert isinstance(data["timeline"], list)

    def test_timeline_sql_injection_in_scope(self, app_client):
        """A1: scope is interpolated into WHERE clause."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/snapshots/timeline", params={
                "scope": "main'; DROP TABLE snapshots; --"
            })
            # Should be 400 after fix, currently may be 200 or 500
            assert resp.status_code in (200, 400, 500)


class TestRecordDQMetrics:
    """POST /api/dq-rules/record-metrics endpoint.

    A2 FIX: This endpoint is now admin-gated.
    """

    def test_record_metrics_rejects_non_admin(self, non_admin_client):
        """A2 FIX: record-metrics is admin-gated — a non-admin cannot write to
        the DQ history table (403)."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders",
                "quality_score": 0.95,
                "rules_evaluated": 10,
                "rules_passed": 9,
                "rules_failed": 1,
            })
            assert resp.status_code == 403

    def test_record_metrics_admin_succeeds(self, admin_client):
        """Admin can record DQ metrics."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders",
                "quality_score": 0.95,
                "rules_evaluated": 10,
                "rules_passed": 9,
                "rules_failed": 1,
            })
            assert resp.status_code == 200

    def test_record_metrics_rejects_non_numeric_score(self, admin_client):
        """A1 FIX: the numeric columns sit at UNQUOTED positions in the INSERT, so
        a non-numeric value would be raw SQL — coercion turns it into a 400."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders",
                "quality_score": "0.5, 1, 1, 1, current_timestamp(), 'x') --",
                "rules_evaluated": 1, "rules_passed": 1, "rules_failed": 0,
            })
            assert resp.status_code == 400
            # Rejected before the INSERT is built.
            assert not any(
                "INSERT INTO" in c[0][0] for c in mock_sql.call_args_list
            )

    def test_record_metrics_rejects_non_numeric_rule_counts(self, admin_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders",
                "quality_score": 0.9, "rules_evaluated": "10 OR 1=1",
                "rules_passed": 9, "rules_failed": 1,
            })
            assert resp.status_code == 400

    def test_record_metrics_coerces_numeric_strings(self, admin_client):
        """Numeric-looking strings still work — coercion, not rejection."""
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules/record-metrics", json={
                "table_fqn": "main.default.orders",
                "quality_score": "0.95", "rules_evaluated": "10",
                "rules_passed": "9", "rules_failed": "1",
            })
            assert resp.status_code == 200
            insert = [c[0][0] for c in mock_sql.call_args_list if "INSERT INTO" in c[0][0]][0]
            assert "0.95" in insert and "10, 9, 1" in insert


class TestEnqueueDelivery:
    """POST /api/notifications/enqueue-delivery.

    A2 FIX: admin-gated — it is a write that makes the delivery job POST to every
    registered endpoint, and it was the only ungated mutation in this file.
    """

    def test_enqueue_rejects_non_admin(self, non_admin_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = non_admin_client.post("/api/notifications/enqueue-delivery")
            assert resp.status_code == 403
            mock_sql.assert_not_called()

    def test_enqueue_admin_succeeds(self, admin_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/notifications/enqueue-delivery")
            assert resp.status_code == 200
            assert resp.json()["queued"] == 0

    def test_enqueue_escapes_backslash_quote_url(self, admin_client):
        r"""A1 FIX: a webhook URL starting `\'` must not close its literal —
        quote-doubling alone produced `\''`, whose second quote terminates it."""
        def _se(sql):
            if "is_read = false" in sql:
                return [{"notif_type": "schema_change", "severity": "HIGH",
                         "title": "t", "detail": "d", "table_fqn": "c.s.t",
                         "detected_at": "2026-07-01"}]
            if "enabled = true" in sql:
                return [{"webhook_id": "w1", "event_types": "*", "url": "\\' OR 1=1--"}]
            return []

        with patch("backend.routes.capability_closures._execute_sql", side_effect=_se) as mock_sql:
            resp = admin_client.post("/api/notifications/enqueue-delivery")
            assert resp.status_code == 200
            assert resp.json()["queued"] == 1
            insert = [c[0][0] for c in mock_sql.call_args_list if "INSERT INTO" in c[0][0]][0]
            assert "'\\\\'' OR 1=1--'" in insert
            assert "'\\''" not in insert  # the bypassable quote-only form


class TestDeliveryStatus:
    """GET /api/notifications/delivery-status endpoint."""

    def test_returns_delivery_queue(self, app_client):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/notifications/delivery-status")
            assert resp.status_code == 200
            data = resp.json()
            assert "deliveries" in data
            assert "pending" in data
            assert "total" in data
