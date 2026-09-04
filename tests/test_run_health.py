"""Tests for the per-node run health check (v2.6.0).

Covers backend.server.observability.get_recent_runs (per-run status, duration,
per-run cost joined from system.billing, verdict / success-rate / duration-trend
/ cost-total + spike, deep links, entity dispatch) and the
GET /api/observability/runs route (validation + cached read-through).
"""
from unittest.mock import patch, MagicMock

import pytest


# Two timeline rows (newest first) + a billing row per run.
def _pipeline_timeline():
    return [
        {"run_id": "u2", "result_state": "COMPLETED", "started_at": "2026-07-10T00:00:00Z",
         "ended_at": "2026-07-10T00:20:00Z", "duration_seconds": 1200},
        {"run_id": "u1", "result_state": "COMPLETED", "started_at": "2026-07-09T00:00:00Z",
         "ended_at": "2026-07-09T00:10:00Z", "duration_seconds": 600},
    ]


def _cost_rows():
    return [{"run_id": "u2", "cost_usd": 59.6}, {"run_id": "u1", "cost_usd": 41.2}]


class TestGetRecentRuns:
    def test_unsupported_entity_type(self):
        from backend.server.observability import get_recent_runs
        out = get_recent_runs("NOTEBOOK", "abc")
        assert out["runs"] == []
        assert "only supported" in out.get("detail", "")

    def test_pipeline_runs_with_cost_and_summary(self):
        from backend.server import observability as obs
        with patch.object(obs, "_execute_sql", side_effect=[_pipeline_timeline(), _cost_rows()]), \
             patch.object(obs, "_workspace_host", return_value="https://ws.databricks.com"):
            out = obs.get_recent_runs("PIPELINE", "84ad", limit=5)
        assert out["entity_type"] == "PIPELINE"
        assert len(out["runs"]) == 2
        # verdict + success rate: both COMPLETED → healthy, 1.0
        assert out["verdict"] == "healthy"
        assert out["success_rate"] == 1.0
        # per-run cost mapped onto each run
        assert out["runs"][0]["cost_usd"] == 59.6
        # total across runs
        assert out["total_cost_usd"] == pytest.approx(100.8, abs=0.01)
        # avg duration
        assert out["avg_duration_seconds"] == pytest.approx(900.0, abs=0.1)
        # latest (1200s) is >1.25x the prior (600s) → trend up
        assert out["duration_trend"] == "up"
        # pipeline entity url
        assert out["entity_url"].endswith("/#joblist/pipelines/84ad")

    def test_job_success_states_and_run_url(self):
        from backend.server import observability as obs
        timeline = [
            {"run_id": "r1", "result_state": "SUCCEEDED", "started_at": "2026-07-10T00:00:00Z",
             "ended_at": "2026-07-10T00:05:00Z", "duration_seconds": 300},
        ]
        with patch.object(obs, "_execute_sql", side_effect=[timeline, []]), \
             patch.object(obs, "_workspace_host", return_value="https://ws.databricks.com"):
            out = obs.get_recent_runs("JOB", "123", limit=5)
        assert out["verdict"] == "healthy"
        assert out["runs"][0]["succeeded"] is True
        # job run deep link includes run id
        assert out["runs"][0]["run_url"].endswith("/#job/123/run/r1")

    def test_failing_verdict_when_runs_fail(self):
        from backend.server import observability as obs
        timeline = [
            {"run_id": "r2", "result_state": "FAILED", "started_at": "2026-07-10T00:00:00Z",
             "ended_at": "2026-07-10T00:05:00Z", "duration_seconds": 300},
            {"run_id": "r1", "result_state": "FAILED", "started_at": "2026-07-09T00:00:00Z",
             "ended_at": "2026-07-09T00:05:00Z", "duration_seconds": 300},
        ]
        with patch.object(obs, "_execute_sql", side_effect=[timeline, []]), \
             patch.object(obs, "_workspace_host", return_value=""):
            out = obs.get_recent_runs("JOB", "123")
        assert out["verdict"] == "failing"
        assert out["success_rate"] == 0.0
        assert out["runs"][0]["succeeded"] is False

    def test_cost_spike_flagged(self):
        """A run costing >2x the median of the others is flagged."""
        from backend.server import observability as obs
        timeline = [
            {"run_id": "r3", "result_state": "SUCCEEDED", "started_at": "2026-07-12T00:00:00Z",
             "ended_at": "2026-07-12T00:05:00Z", "duration_seconds": 300},
            {"run_id": "r2", "result_state": "SUCCEEDED", "started_at": "2026-07-11T00:00:00Z",
             "ended_at": "2026-07-11T00:05:00Z", "duration_seconds": 300},
            {"run_id": "r1", "result_state": "SUCCEEDED", "started_at": "2026-07-10T00:00:00Z",
             "ended_at": "2026-07-10T00:05:00Z", "duration_seconds": 300},
        ]
        cost = [{"run_id": "r3", "cost_usd": 100.0}, {"run_id": "r2", "cost_usd": 10.0},
                {"run_id": "r1", "cost_usd": 8.0}]
        with patch.object(obs, "_execute_sql", side_effect=[timeline, cost]), \
             patch.object(obs, "_workspace_host", return_value=""):
            out = obs.get_recent_runs("JOB", "123")
        assert out["cost_spike_run_id"] == "r3"

    def test_missing_billing_yields_null_costs_not_error(self):
        """C1/A10: no SELECT on system.billing → costs blank, still returns runs."""
        from backend.server import observability as obs
        # timeline OK, cost query raises → caught, costs stay None
        with patch.object(obs, "_execute_sql", side_effect=[_pipeline_timeline(), RuntimeError("no billing")]), \
             patch.object(obs, "_workspace_host", return_value=""):
            out = obs.get_recent_runs("PIPELINE", "84ad")
        assert len(out["runs"]) == 2
        assert out["runs"][0]["cost_usd"] is None
        assert out["total_cost_usd"] is None

    def test_timeline_error_returns_empty_not_raises(self):
        from backend.server import observability as obs
        with patch.object(obs, "_execute_sql", side_effect=RuntimeError("no lakeflow")), \
             patch.object(obs, "_workspace_host", return_value=""):
            out = obs.get_recent_runs("JOB", "123")
        assert out["runs"] == []
        assert out["verdict"] == "unknown"


class TestObservabilityRunsRoute:
    """GET /api/observability/runs."""

    def test_bad_entity_type_400(self, app_client):
        resp = app_client.get("/api/observability/runs", params={
            "entity_type": "NOTEBOOK", "entity_id": "123"})
        assert resp.status_code == 400

    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/observability/runs")
        assert resp.status_code in (400, 422)

    def test_limit_out_of_bounds_422(self, app_client):
        resp = app_client.get("/api/observability/runs", params={
            "entity_type": "JOB", "entity_id": "123", "limit": 999})
        assert resp.status_code == 422

    def test_injection_in_entity_id_400(self, app_client):
        resp = app_client.get("/api/observability/runs", params={
            "entity_type": "JOB", "entity_id": "123'; DROP TABLE--"})
        assert resp.status_code == 400

    def test_valid_request_returns_payload(self, app_client):
        # Bypass the capability cache so the route computes live.
        with patch("backend.server.capability_cache.CapabilityCache.get", return_value=None), \
             patch("backend.server.capability_cache.CapabilityCache.set", return_value=True), \
             patch("backend.server.observability._execute_sql", side_effect=[_pipeline_timeline(), _cost_rows()]), \
             patch("backend.server.observability._workspace_host", return_value="https://ws.databricks.com"):
            resp = app_client.get("/api/observability/runs", params={
                "entity_type": "PIPELINE", "entity_id": "84ad", "limit": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["verdict"] == "healthy"
        assert len(data["runs"]) == 2
