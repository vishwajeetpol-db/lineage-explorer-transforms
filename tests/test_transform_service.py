"""Unit tests for backend/transform_service.py.

The service reads pre-built column-level transformation edges from Delta tables
and performs BFS backtracking. Everything hits the network through the module's
private ``_sql`` executor (which calls ``_get_client``), so we patch ``_sql``
directly with canned (columns, rows) tuples. Fast + offline.

The module caches results in an in-memory TTL cache keyed by args; each test
clears that cache first so patched ``_sql`` returns are actually consulted.
"""
from unittest.mock import patch

import pytest

import backend.transform_service as ts


@pytest.fixture(autouse=True)
def _clear_transform_cache():
    """Flush the module cache before AND after each test so cached results from a
    prior test never mask a fresh patched _sql return."""
    ts.invalidate_transform_cache()
    yield
    ts.invalidate_transform_cache()


# ---------------------------------------------------------------------------
# get_transform_freshness
# ---------------------------------------------------------------------------
class TestFreshness:
    def test_no_rows_never_built(self):
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.exists is False
        assert info.age_str == "Never built"
        assert info.is_stale is True

    def test_zero_count_never_built(self):
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[0, None]])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.exists is False
        assert info.edge_count == 0

    def test_fresh_recent_minutes(self):
        from datetime import datetime, timezone, timedelta
        recent = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[5, recent]])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.exists is True
        assert info.edge_count == 5
        assert info.age_str.endswith("m ago")
        assert info.is_stale is False

    def test_hours_ago(self):
        from datetime import datetime, timezone, timedelta
        ago = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[2, ago]])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.age_str.endswith("h ago")
        assert info.is_stale is False

    def test_days_ago_is_stale(self):
        from datetime import datetime, timezone, timedelta
        ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[2, ago]])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.age_str.endswith("d ago")
        assert info.is_stale is True

    def test_unparseable_timestamp_is_stale(self):
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[2, "not-a-date"]])):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.exists is True
        assert info.is_stale is True

    def test_sql_error_returns_unknown(self):
        with patch.object(ts, "_sql", side_effect=RuntimeError("boom")):
            info = ts.get_transform_freshness("c", "s", "t")
        assert info.exists is False
        assert info.age_str == "Unknown"
        assert info.is_stale is True


# ---------------------------------------------------------------------------
# diagnose_missing_lineage
# ---------------------------------------------------------------------------
class TestDiagnose:
    def test_query_error_unknown(self):
        with patch.object(ts, "_sql", side_effect=RuntimeError("no access")):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "unknown"

    def test_no_producer(self):
        # total == 0
        with patch.object(ts, "_sql",
                          return_value=(["last_produced", "in_window", "total"], [[None, 0, 0]])):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "no_producer"

    def test_producer_outside_window(self):
        from datetime import datetime, timezone, timedelta
        lp = (datetime.now(timezone.utc) - timedelta(days=400)).isoformat()
        with patch.object(ts, "_sql",
                          return_value=(["last_produced", "in_window", "total"], [[lp, 0, 3]])):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "producer_outside_window"
        assert d.in_window is False
        assert d.days_ago is not None

    def test_producer_outside_window_unparseable_date(self):
        # last_produced present but not ISO → days_ago stays None
        with patch.object(ts, "_sql",
                          return_value=(["last_produced", "in_window", "total"], [["weird", 0, 3]])):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "producer_outside_window"
        assert d.days_ago is None

    def test_producer_unresolved_no_resolvable_tasks(self):
        from datetime import datetime, timezone, timedelta
        lp = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        # in_window > 0 → producer_unresolved; skip reasons include no_resolvable_tasks
        def fake_sql(stmt):
            if "column_lineage" in stmt:
                return (["last_produced", "in_window", "total"], [[lp, 2, 5]])
            # extraction report lookup
            import json
            return (["report_json"], [[json.dumps({"skip_reasons": {"no_resolvable_tasks": 1}})]])
        with patch.object(ts, "_sql", side_effect=fake_sql):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "producer_unresolved"
        assert "no_resolvable_tasks" in d.skip_reasons
        assert "couldn't be read" in d.title

    def test_producer_unresolved_generic(self):
        from datetime import datetime, timezone, timedelta
        lp = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        def fake_sql(stmt):
            if "column_lineage" in stmt:
                return (["last_produced", "in_window", "total"], [[lp, 1, 4]])
            return (["report_json"], [])  # no extraction report
        with patch.object(ts, "_sql", side_effect=fake_sql):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "producer_unresolved"
        assert d.skip_reasons == []

    def test_empty_rows_defaults_to_no_producer(self):
        # rows empty → r = [None,0,0] → total 0 → no_producer
        with patch.object(ts, "_sql",
                          return_value=(["last_produced", "in_window", "total"], [])):
            d = ts.diagnose_missing_lineage("c", "s", "t")
        assert d.reason_code == "no_producer"


# ---------------------------------------------------------------------------
# _latest_extraction_skip_reasons (helper)
# ---------------------------------------------------------------------------
class TestSkipReasons:
    def test_parses_report(self):
        import json
        rep = json.dumps({"skip_reasons": {"a": 1, "b": 2}})
        with patch.object(ts, "_sql", return_value=(["report_json"], [[rep]])):
            reasons = ts._latest_extraction_skip_reasons()
        assert set(reasons) == {"a", "b"}

    def test_no_rows_empty(self):
        with patch.object(ts, "_sql", return_value=(["report_json"], [])):
            assert ts._latest_extraction_skip_reasons() == []

    def test_error_empty(self):
        with patch.object(ts, "_sql", side_effect=RuntimeError("x")):
            assert ts._latest_extraction_skip_reasons() == []


# ---------------------------------------------------------------------------
# _get_latest_run_id + load_edges
# ---------------------------------------------------------------------------
class TestLatestRunId:
    def test_with_table_fqn(self):
        with patch.object(ts, "_sql", return_value=(["pipeline_run_id"], [["run-1"]])):
            rid = ts._get_latest_run_id(table_fqn="c.s.t")
        assert rid == "run-1"

    def test_global(self):
        with patch.object(ts, "_sql", return_value=(["pipeline_run_id"], [["run-g"]])):
            rid = ts._get_latest_run_id()
        assert rid == "run-g"

    def test_no_rows_none(self):
        with patch.object(ts, "_sql", return_value=(["pipeline_run_id"], [])):
            assert ts._get_latest_run_id(table_fqn="c.s.t") is None


def _edge_row(src_col, dst_col, src_tbl="c.s.up", dst_tbl="c.s.t",
              category="ARITHMETIC", edge_id="e1"):
    """Build a dict edge as load_edges would return it."""
    return {
        "src_node_id": f"col:{src_tbl}::{src_col}",
        "src_fqn": src_tbl,
        "src_col": src_col,
        "dst_node_id": f"col:{dst_tbl}::{dst_col}",
        "dst_fqn": dst_tbl,
        "dst_col": dst_col,
        "edge_id": edge_id,
        "source_path": "/nb/etl.py",
        "expr": "a + b",
        "expr_sql": "a + b",
        "transform_category": category,
    }


class TestLoadEdges:
    def test_no_run_returns_empty(self):
        with patch.object(ts, "_get_latest_run_id", return_value=None):
            assert ts.load_edges("c.s.t") == []

    def test_returns_dicts(self):
        cols = ["src_node_id", "src_fqn", "src_col", "dst_node_id", "dst_fqn",
                "dst_col", "edge_id", "source_path", "expr", "expr_sql", "transform_category"]
        row = ["col:c.s.up::x", "c.s.up", "x", "col:c.s.t::y", "c.s.t", "y",
               "e1", "/nb", "x", "x", "CAST"]
        with patch.object(ts, "_get_latest_run_id", return_value="run-1"), \
             patch.object(ts, "_sql", return_value=(cols, [row])):
            edges = ts.load_edges("c.s.t")
        assert len(edges) == 1
        assert edges[0]["dst_col"] == "y"


# ---------------------------------------------------------------------------
# backtrack_transform_lineage
# ---------------------------------------------------------------------------
class TestBacktrack:
    def test_no_edges_no_lineage(self):
        with patch.object(ts, "load_edges", return_value=[]):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        assert resp.has_lineage is False
        assert resp.levels == []

    def test_target_not_in_upstream_index(self):
        # edges exist but none target col:c.s.t::y
        edges = [_edge_row("a", "z")]  # dst is col:c.s.t::z, not ::y
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        assert resp.has_lineage is False

    def test_target_is_source_column(self):
        # target col:c.s.t::y appears only as a SOURCE
        edges = [_edge_row("y", "final", src_tbl="c.s.t", dst_tbl="c.s.down")]
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        assert resp.has_lineage is False
        assert resp.is_source_column is True

    def test_single_hop_lineage(self):
        edges = [_edge_row("x", "y")]  # col:c.s.up::x -> col:c.s.t::y
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        assert resp.has_lineage is True
        assert len(resp.levels) == 2  # target + 1 upstream
        assert resp.total_edges == 1
        # transform edge category color resolved
        te = resp.levels[1].transforms[0]
        assert te.category == "ARITHMETIC"
        assert te.category_color == ts.TRANSFORM_CATEGORIES["ARITHMETIC"]

    def test_multi_hop_and_dedup(self):
        # y <- x (up), x <- w (up2); plus a duplicate x->y edge to exercise dedup
        edges = [
            _edge_row("x", "y", src_tbl="c.s.up", dst_tbl="c.s.t", edge_id="e1"),
            _edge_row("x", "y", src_tbl="c.s.up", dst_tbl="c.s.t", edge_id="e2"),  # dup pair
            _edge_row("w", "x", src_tbl="c.s.up2", dst_tbl="c.s.up", edge_id="e3"),
        ]
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        assert resp.has_lineage is True
        # dedup: only one x->y edge kept in level 1
        assert len(resp.levels[1].transforms) == 1
        # level 2 has w->x
        assert len(resp.levels) == 3

    def test_unknown_category_default_color(self):
        edges = [_edge_row("x", "y", category=None)]
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        te = resp.levels[1].transforms[0]
        assert te.category == "UNKNOWN"
        assert te.category_color == "#6B7280"

    def test_max_depth_limits_traversal(self):
        edges = [
            _edge_row("x", "y", src_tbl="c.s.up", dst_tbl="c.s.t"),
            _edge_row("w", "x", src_tbl="c.s.up2", dst_tbl="c.s.up"),
        ]
        with patch.object(ts, "load_edges", return_value=edges):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y", max_depth=1)
        # only 1 upstream layer despite a 2-hop chain
        assert len(resp.levels) == 2

    def test_captured_expression_attached(self):
        edges = [_edge_row("x", "y")]
        with patch.object(ts, "load_edges", return_value=edges), \
             patch.object(ts, "_get_captured_expression_for_node",
                          return_value={"expression": "CAPTURED"}):
            resp = ts.backtrack_transform_lineage("c", "s", "t", "y")
        # target node (level 0) picks up the captured expression
        assert resp.levels[0].nodes[0].captured_expression == "CAPTURED"
        assert resp.levels[1].nodes[0].captured_expression == "CAPTURED"


# ---------------------------------------------------------------------------
# _get_captured_expression_for_node
# ---------------------------------------------------------------------------
class TestCapturedExpressionForNode:
    def test_flag_off_returns_none(self):
        with patch("backend.feature_flags.get_flag_state", return_value=False):
            assert ts._get_captured_expression_for_node("c.s.t", "col") is None

    def test_bad_fqn_returns_none(self):
        with patch("backend.feature_flags.get_flag_state", return_value=True):
            # not a 3-part fqn
            assert ts._get_captured_expression_for_node("nope", "col") is None

    def test_flag_on_delegates(self):
        with patch("backend.feature_flags.get_flag_state", return_value=True):
            import backend.plan_capture_service as pcs
            with patch.object(pcs, "get_captured_expression",
                              return_value={"expression": "X"}):
                out = ts._get_captured_expression_for_node("c.s.t", "col")
        assert out == {"expression": "X"}

    def test_exception_returns_none(self):
        with patch("backend.feature_flags.get_flag_state", side_effect=RuntimeError("x")):
            assert ts._get_captured_expression_for_node("c.s.t", "col") is None


# ---------------------------------------------------------------------------
# invalidate + categories + snapshot + clear
# ---------------------------------------------------------------------------
class TestCategoriesAndCache:
    def test_categories(self):
        cats = ts.get_transform_categories()
        assert "ARITHMETIC" in cats
        assert cats["CAST"].startswith("#")

    def test_invalidate_clears(self):
        # seed cache via a freshness call
        with patch.object(ts, "_sql", return_value=(["cnt", "last_built"], [[0, None]])):
            ts.get_transform_freshness("c", "s", "t")
        ts.invalidate_transform_cache()
        with ts._transform_cache_lock:
            assert len(ts._transform_cache) == 0

    def test_snapshot(self):
        snap = ts.get_transform_cache_snapshot()
        assert set(snap) >= {"entries", "current_size_mb", "max_size_mb", "ttl_seconds"}


class TestClearTransformLineage:
    def test_scope_cache(self):
        out = ts.clear_transform_lineage("cache")
        assert out["scope"] == "cache"
        assert out["cleared"] == []

    def test_scope_table_requires_fqn(self):
        out = ts.clear_transform_lineage("table", None)
        assert "error" in out

    def test_scope_table_ok(self):
        with patch.object(ts, "_sql", return_value=([], [])) as mock_sql:
            out = ts.clear_transform_lineage("table", "c.s.t")
        assert out["scope"] == "table"
        assert "lineage_edge_endpoints" in out["cleared"]
        assert mock_sql.called

    def test_scope_table_nodes_edges_error_swallowed(self):
        # first _sql (edge_endpoints) ok, subsequent nodes/edges deletes raise
        calls = {"n": 0}
        def fake_sql(stmt):
            calls["n"] += 1
            if calls["n"] == 1:
                return ([], [])
            raise RuntimeError("no perms")
        with patch.object(ts, "_sql", side_effect=fake_sql):
            out = ts.clear_transform_lineage("table", "c.s.t")
        # edge_endpoints still recorded despite the later failure
        assert out["cleared"] == ["lineage_edge_endpoints"]

    def test_scope_all(self):
        with patch.object(ts, "_sql", return_value=([], [])):
            out = ts.clear_transform_lineage("all")
        assert out["scope"] == "all"
        assert set(out["cleared"]) == set(ts._CLEARABLE_STORE_TABLES)

    def test_scope_all_partial_failure(self):
        def fake_sql(stmt):
            if "lineage_nodes" in stmt:
                raise RuntimeError("boom")
            return ([], [])
        with patch.object(ts, "_sql", side_effect=fake_sql):
            out = ts.clear_transform_lineage("all")
        assert "lineage_nodes" not in out["cleared"]
        assert "lineage_edge_endpoints" in out["cleared"]


# ---------------------------------------------------------------------------
# _sql executor
# ---------------------------------------------------------------------------
class TestSqlExecutor:
    def _resp(self, state, cols=None, rows=None, err=None):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status.state = state
        resp.status.error = MagicMock(message=err) if err else None
        col_objs = [MagicMock(name=c) for c in (cols or [])]
        # MagicMock(name=..) sets the repr, not .name attribute — set explicitly
        for co, c in zip(col_objs, cols or []):
            co.name = c
        resp.manifest.schema.columns = col_objs
        resp.result.data_array = rows
        return resp

    def test_success(self):
        from databricks.sdk.service.sql import StatementState
        from unittest.mock import MagicMock
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.SUCCEEDED, cols=["a", "b"], rows=[[1, 2]])
        with patch.object(ts, "_get_client", return_value=client):
            cols, rows = ts._sql("SELECT 1")
        assert cols == ["a", "b"]
        assert rows == [[1, 2]]

    def test_failure_raises(self):
        from databricks.sdk.service.sql import StatementState
        from unittest.mock import MagicMock
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = self._resp(
            StatementState.FAILED, err="syntax error")
        with patch.object(ts, "_get_client", return_value=client):
            with pytest.raises(RuntimeError, match="Transform SQL failed"):
                ts._sql("SELECT bad")

    def test_no_result_empty_rows(self):
        from databricks.sdk.service.sql import StatementState
        from unittest.mock import MagicMock
        client = MagicMock()
        resp = self._resp(StatementState.SUCCEEDED, cols=["a"], rows=None)
        resp.result = None
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(ts, "_get_client", return_value=client):
            cols, rows = ts._sql("SELECT 1")
        assert rows == []
