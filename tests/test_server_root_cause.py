"""Unit tests for backend.server.root_cause.

Covers:
- _execute_sql (no-warehouse, success, non-success, empty result)
- _walk_upstream_columns BFS (rows, visited/hop cutoff, error branch)
- _get_failed_runs_around (with/without anomaly window, exception)
- _get_dq_violations (no rules, no expr, violations found, exec error, query error)
- trace_root_cause (upstream error, no upstream, full scoring + ERROR boost + cap)
- _walk_upstream_tables / _producers_for_table (success + error)
- _producer_health (JOB/PIPELINE/other, failed/stale/healthy/no_history)
- trace_root_cause_table (counts, prime suspect, failure path, source-table skip)
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState

import backend.server.root_cause as rc


def _make_client(columns=None, data=None, state=StatementState.SUCCEEDED, error_msg=None):
    """Build a mock WorkspaceClient whose execute_statement returns a canned resp."""
    resp = MagicMock()
    resp.status.state = state
    if error_msg is not None:
        resp.status.error.message = error_msg
    else:
        resp.status.error = None
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        cols = []
        for name in (columns or []):
            c = MagicMock()
            c.name = name
            cols.append(c)
        resp.manifest.schema.columns = cols
    client = MagicMock()
    client.statement_execution.execute_statement.return_value = resp
    return client


class TestExecuteSql:
    def test_no_warehouse_raises(self):
        with patch.object(rc, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                rc._execute_sql("SELECT 1")

    def test_success_maps_rows(self):
        client = _make_client(columns=["a", "b"], data=[[1, 2], [3, 4]])
        with patch.object(rc, "WAREHOUSE_ID", "wh"), patch.object(rc, "_get_client", return_value=client):
            out = rc._execute_sql("SELECT a, b")
        assert out == [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    def test_non_success_raises_with_error_message(self):
        client = _make_client(state=StatementState.FAILED, error_msg="boom")
        with patch.object(rc, "WAREHOUSE_ID", "wh"), patch.object(rc, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="boom"):
                rc._execute_sql("SELECT 1")

    def test_empty_result_returns_empty_list(self):
        client = _make_client(data=None)
        with patch.object(rc, "WAREHOUSE_ID", "wh"), patch.object(rc, "_get_client", return_value=client):
            assert rc._execute_sql("SELECT 1") == []


class TestWalkUpstreamColumns:
    def test_walks_and_dedupes(self):
        calls = {"n": 0}

        def fake_sql(sql):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"source_table_full_name": "c.s.up", "source_column_name": "col_up"}]
            return []  # up has no further upstream

        with patch.object(rc, "_execute_sql", side_effect=fake_sql):
            path = rc._walk_upstream_columns("c.s.t", "col", max_hops=4)
        assert path == [{"table": "c.s.up", "column": "col_up", "hop": 1}]

    def test_skips_rows_missing_table_or_column(self):
        with patch.object(rc, "_execute_sql", return_value=[{"source_table_full_name": "", "source_column_name": "x"}]):
            assert rc._walk_upstream_columns("c.s.t", "col") == []

    def test_error_is_swallowed(self):
        with patch.object(rc, "_execute_sql", side_effect=RuntimeError("nope")):
            assert rc._walk_upstream_columns("c.s.t", "col") == []

    def test_hop_cutoff(self):
        with patch.object(rc, "_execute_sql") as m:
            rc._walk_upstream_columns("c.s.t", "col", max_hops=0)
            m.assert_not_called()


class TestGetFailedRunsAround:
    def test_with_anomaly_window(self):
        rows = [{"job_id": 12, "result_state": "FAILED", "period_start_time": "2026-01-01"}]
        with patch.object(rc, "_execute_sql", return_value=rows) as m:
            ev = rc._get_failed_runs_around("c.s.t", "2026-01-01T00:00:00")
        assert ev[0]["type"] == "failed_job_run"
        assert ev[0]["entity_id"] == "12"
        assert "INTERVAL" in m.call_args[0][0]

    def test_without_anomaly(self):
        with patch.object(rc, "_execute_sql", return_value=[]) as m:
            assert rc._get_failed_runs_around("c.s.t", None) == []
        assert "INTERVAL" not in m.call_args[0][0]

    def test_exception_returns_empty(self):
        with patch.object(rc, "_execute_sql", side_effect=RuntimeError("x")):
            assert rc._get_failed_runs_around("c.s.t", None) == []


class TestGetDqViolations:
    def test_no_rules(self):
        with patch.object(rc, "_execute_sql", return_value=[]):
            assert rc._get_dq_violations("c.s.t", "col") == []

    def test_rule_without_expression_skipped(self):
        rules = [{"rule_id": "r1", "expression": ""}]
        with patch.object(rc, "_execute_sql", return_value=rules):
            assert rc._get_dq_violations("c.s.t") == []

    def test_violations_found(self):
        rules = [{"rule_id": "r1", "column_name": "col", "rule_type": "not_null",
                  "expression": "col IS NOT NULL", "severity": "ERROR"}]
        sample = [{"total": 100, "violations": 5}]

        def fake_sql(sql):
            if "COUNT(*)" in sql:
                return sample
            return rules

        with patch.object(rc, "_execute_sql", side_effect=fake_sql):
            ev = rc._get_dq_violations("c.s.t", "col")
        assert ev[0]["type"] == "dq_violation"
        assert ev[0]["violations"] == 5
        assert ev[0]["pass_rate"] == 0.95

    def test_no_violations(self):
        rules = [{"rule_id": "r1", "expression": "x", "severity": "WARN"}]
        sample = [{"total": 10, "violations": 0}]

        def fake_sql(sql):
            return sample if "COUNT(*)" in sql else rules

        with patch.object(rc, "_execute_sql", side_effect=fake_sql):
            assert rc._get_dq_violations("c.s.t") == []

    def test_rule_exec_error_swallowed(self):
        rules = [{"rule_id": "r1", "expression": "bad"}]

        def fake_sql(sql):
            if "COUNT(*)" in sql:
                raise RuntimeError("bad sql")
            return rules

        with patch.object(rc, "_execute_sql", side_effect=fake_sql):
            assert rc._get_dq_violations("c.s.t") == []

    def test_query_error_swallowed(self):
        with patch.object(rc, "_execute_sql", side_effect=RuntimeError("nope")):
            assert rc._get_dq_violations("c.s.t") == []


class TestTraceRootCause:
    def test_upstream_walk_error(self):
        with patch.object(rc, "_walk_upstream_columns", side_effect=RuntimeError("walk fail")):
            out = rc.trace_root_cause("c", "s", "t", "col")
        assert out["error"] == "walk fail"

    def test_no_upstream(self):
        with patch.object(rc, "_walk_upstream_columns", return_value=[]):
            out = rc.trace_root_cause("c", "s", "t", "col")
        assert "root/source column" in out["detail"]

    def test_full_scoring_with_error_boost_and_cap(self):
        upstream = [
            {"table": "c.s.a", "column": "x", "hop": 1},
            {"table": "c.s.a", "column": "x", "hop": 1},  # duplicate collapsed
            {"table": "c.s.b", "column": "y", "hop": 2},
        ]
        run_ev = [{"type": "failed_job_run"}]
        dq_ev = [{"type": "dq_violation", "severity": "ERROR"}]
        with patch.object(rc, "_walk_upstream_columns", return_value=upstream), \
             patch.object(rc, "_get_failed_runs_around", return_value=run_ev), \
             patch.object(rc, "_get_dq_violations", return_value=dq_ev):
            out = rc.trace_root_cause("c", "s", "t", "col")
        cands = out["candidates"]
        assert len(cands) == 2  # dedup on table::column
        top = cands[0]
        # hop1: 0.5 + 0.3 + 0.4 = 1.2, capped at 1.0
        assert top["score"] == 1.0
        assert top["table"] == "c.s.a"

    def test_scoring_without_evidence(self):
        upstream = [{"table": "c.s.a", "column": "x", "hop": 1}]
        with patch.object(rc, "_walk_upstream_columns", return_value=upstream), \
             patch.object(rc, "_get_failed_runs_around", return_value=[]), \
             patch.object(rc, "_get_dq_violations", return_value=[]):
            out = rc.trace_root_cause("c", "s", "t", "col")
        assert out["candidates"][0]["score"] == 0.5


class TestWalkUpstreamTables:
    def test_bfs_collects_min_hop(self):
        calls = {"n": 0}

        def fake_sql(sql):
            calls["n"] += 1
            if calls["n"] == 1:
                return [{"source_table_full_name": "c.s.up"}]
            return []

        with patch.object(rc, "_execute_sql", side_effect=fake_sql):
            out = rc._walk_upstream_tables("c.s.t", max_hops=3)
        assert out == {"c.s.up": 1}

    def test_error_breaks(self):
        with patch.object(rc, "_execute_sql", side_effect=RuntimeError("x")):
            assert rc._walk_upstream_tables("c.s.t") == {}


class TestProducersForTable:
    def test_success(self):
        rows = [{"entity_type": "JOB", "entity_id": 7}]
        with patch.object(rc, "_execute_sql", return_value=rows):
            out = rc._producers_for_table("c.s.t")
        assert out == [{"entity_type": "JOB", "entity_id": "7"}]

    def test_error(self):
        with patch.object(rc, "_execute_sql", side_effect=RuntimeError("x")):
            assert rc._producers_for_table("c.s.t") == []


class TestProducerHealth:
    def test_job_failed(self):
        h = {"total_runs": 3, "last_run_result": "FAILED", "last_run_at": "2026-07-30T00:00:00Z",
             "success_rate": 0.5}
        with patch("backend.server.observability.get_job_run_health", return_value=h):
            out = rc._producer_health("JOB", "1")
        assert out["status"] == "failed"

    def test_job_no_history(self):
        with patch("backend.server.observability.get_job_run_health", return_value={"total_runs": 0}):
            out = rc._producer_health("JOB", "1")
        assert out["status"] == "no_history"

    def test_pipeline_healthy_recent(self):
        # Use a dynamically-recent timestamp (well within STALE_DAYS) so the test
        # doesn't age into "stale" as wall-clock time passes.
        recent = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        h = {"total_updates": 2, "last_update_state": "COMPLETED",
             "last_update_at": recent, "success_rate": 1.0}
        with patch("backend.server.observability.get_pipeline_update_health", return_value=h):
            out = rc._producer_health("PIPELINE", "p1")
        assert out["status"] == "healthy"

    def test_pipeline_stale(self):
        h = {"total_updates": 2, "last_update_state": "COMPLETED",
             "last_update_at": "2000-01-01T00:00:00Z", "success_rate": 1.0}
        with patch("backend.server.observability.get_pipeline_update_health", return_value=h):
            out = rc._producer_health("PIPELINE", "p1")
        assert out["status"] == "stale"

    def test_healthy_unparseable_date(self):
        h = {"total_runs": 1, "last_run_result": "COMPLETED", "last_run_at": "not-a-date",
             "success_rate": 1.0}
        with patch("backend.server.observability.get_job_run_health", return_value=h):
            out = rc._producer_health("JOB", "1")
        assert out["status"] == "healthy"

    def test_unknown_entity_type(self):
        out = rc._producer_health("NOTEBOOK", "n1")
        assert out["status"] == "no_history"


class TestTraceRootCauseTable:
    def test_full_flow_with_prime_suspect(self):
        def fake_producers(tbl):
            if tbl == "c.s.up":
                return [{"entity_type": "JOB", "entity_id": "1"}]
            if tbl == "c.s.t":
                return [{"entity_type": "JOB", "entity_id": "2"}]
            return []

        def fake_health(et, eid):
            if eid == "1":
                return {"entity_type": et, "entity_id": eid, "status": "failed",
                        "last_result": "FAILED", "last_run_at": None, "success_rate": 0.0}
            return {"entity_type": et, "entity_id": eid, "status": "healthy",
                    "last_result": "COMPLETED", "last_run_at": None, "success_rate": 1.0}

        with patch.object(rc, "_walk_upstream_tables", return_value={"c.s.up": 1}), \
             patch.object(rc, "_producers_for_table", side_effect=fake_producers), \
             patch.object(rc, "_producer_health", side_effect=fake_health):
            out = rc.trace_root_cause_table("c", "s", "t")
        assert out["focus_table"] == "c.s.t"
        assert out["counts"]["failed"] == 1
        assert out["prime_suspect"]["table"] == "c.s.up"
        assert out["failure_path"]  # focus -> suspect chain
        assert any(f["is_focus"] for f in out["flagged"])

    def test_source_table_without_producers_skipped(self):
        with patch.object(rc, "_walk_upstream_tables", return_value={}), \
             patch.object(rc, "_producers_for_table", return_value=[]), \
             patch.object(rc, "_producer_health", return_value={}):
            out = rc.trace_root_cause_table("c", "s", "t")
        assert out["flagged"] == []
        assert out["prime_suspect"] is None
        assert out["failure_path"] == []

    def test_focus_only_no_upstream_suspect(self):
        # Focus table itself is the only flagged table (healthy) -> prime = flagged[0], no failure path.
        with patch.object(rc, "_walk_upstream_tables", return_value={}), \
             patch.object(rc, "_producers_for_table", return_value=[{"entity_type": "JOB", "entity_id": "9"}]), \
             patch.object(rc, "_producer_health", return_value={"status": "healthy"}):
            out = rc.trace_root_cause_table("c", "s", "t")
        assert out["prime_suspect"]["is_focus"] is True
        assert out["failure_path"] == []
