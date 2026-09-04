"""Coverage for backend.server.observability aggregate-health functions
(complements test_run_health.py which covers get_recent_runs)."""
from unittest.mock import patch, MagicMock

import backend.server.observability as obs


class TestHealthBadge:
    def test_thresholds(self):
        assert obs._health_badge(0.95, 10) == "healthy"
        assert obs._health_badge(0.80, 10) == "degraded"
        assert obs._health_badge(0.50, 10) == "failing"
        assert obs._health_badge(0.0, 0) == "unknown"


class TestJobRunHealth:
    def test_ok(self):
        agg = [{"total_runs": 10, "successful_runs": 9, "failed_runs": 1,
                "last_run_at": "2026-07-01T00:00:00Z", "avg_duration_seconds": 120.0}]
        last = [{"result_state": "SUCCEEDED"}]
        with patch.object(obs, "_execute_sql", side_effect=[agg, last]):
            out = obs.get_job_run_health("123")
        assert out["total_runs"] == 10
        assert out["health_badge"] == "healthy"
        assert out["last_run_result"] == "SUCCEEDED"

    def test_error_returns_base(self):
        with patch.object(obs, "_execute_sql", side_effect=RuntimeError("no lakeflow")):
            out = obs.get_job_run_health("123")
        assert out["total_runs"] == 0 and out["health_badge"] == "unknown"


class TestPipelineUpdateHealth:
    def test_ok(self):
        agg = [{"total_updates": 5, "successful_updates": 5, "failed_updates": 0,
                "last_update_at": "2026-07-01T00:00:00Z", "avg_duration_seconds": 60.0}]
        last = [{"result_state": "COMPLETED"}]
        with patch.object(obs, "_execute_sql", side_effect=[agg, last]):
            out = obs.get_pipeline_update_health("p1")
        assert out["successful_updates"] == 5
        assert out["health_badge"] == "healthy"


class TestEntityHealthDispatch:
    def test_job(self):
        with patch.object(obs, "get_job_run_health", return_value={"entity_type": "JOB"}) as m:
            assert obs.get_entity_health("JOB", "1")["entity_type"] == "JOB"
            m.assert_called_once()

    def test_pipeline(self):
        with patch.object(obs, "get_pipeline_update_health", return_value={"entity_type": "PIPELINE"}) as m:
            assert obs.get_entity_health("PIPELINE", "1")["entity_type"] == "PIPELINE"
            m.assert_called_once()

    def test_unsupported(self):
        out = obs.get_entity_health("NOTEBOOK", "1")
        assert out["health_badge"] == "unknown"


class TestTableProducerHealth:
    def test_aggregates_producers(self):
        producers = [{"entity_type": "JOB", "entity_id": "1"},
                     {"entity_type": "PIPELINE", "entity_id": "2"}]
        with patch.object(obs, "_execute_sql", return_value=producers), \
             patch.object(obs, "get_entity_health", side_effect=lambda t, i: {"entity_type": t, "entity_id": i}):
            out = obs.get_table_producer_health("c", "s", "t")
        assert len(out) == 2

    def test_error_returns_empty(self):
        with patch.object(obs, "_execute_sql", side_effect=RuntimeError("x")):
            assert obs.get_table_producer_health("c", "s", "t") == []


class TestExecuteSqlAndHost:
    def test_execute_sql_no_warehouse(self):
        import pytest
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}):
            with pytest.raises(Exception):
                obs._execute_sql("SELECT 1")

    def test_workspace_host_ok(self):
        client = MagicMock()
        client.config.host = "https://ws.databricks.com/"
        with patch.object(obs, "_get_client", return_value=client):
            assert obs._workspace_host() == "https://ws.databricks.com"

    def test_workspace_host_error(self):
        with patch.object(obs, "_get_client", side_effect=RuntimeError("x")):
            assert obs._workspace_host() == ""
