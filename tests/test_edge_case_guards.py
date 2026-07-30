"""Coverage-completion tests for backend.edge_case_guards.

Follows tests/test_producer_source.py conventions: everything mocked, fully
offline, and no edits to backend/ source. Covers each guard's ok path plus its
degraded / exception path, calling startup-side-effect guards directly with
mocked deps.
"""
from unittest.mock import patch, MagicMock

import pytest

import backend.edge_case_guards as eg


@pytest.fixture(autouse=True)
def _reset_module_state():
    """Reset the module-level health cache + warm flag so each test is hermetic."""
    eg._health_cache = None
    eg._health_cache_time = 0
    eg._warm_started = False
    yield
    eg._health_cache = None
    eg._health_cache_time = 0
    eg._warm_started = False


# ---------------------------------------------------------------------------
# C1: check_system_tables_health
# ---------------------------------------------------------------------------

class TestCheckSystemTablesHealth:
    def test_ok_path(self):
        fn = MagicMock(return_value=[{"1": 1}])
        status = eg.check_system_tables_health(fn)
        assert status.system_tables_available is True
        assert status.sp_grants_valid is True
        assert status.warehouse_reachable is True

    def test_result_none_marks_unavailable(self):
        fn = MagicMock(return_value=None)
        status = eg.check_system_tables_health(fn)
        assert status.system_tables_available is False
        assert "SELECT on system.access.table_lineage" in status.missing_grants

    def test_table_not_found_exception(self):
        fn = MagicMock(side_effect=Exception("TABLE_OR_VIEW_NOT_FOUND: does not exist"))
        status = eg.check_system_tables_health(fn)
        assert status.system_tables_available is False
        assert "not enabled" in status.error

    def test_permission_denied_exception(self):
        fn = MagicMock(side_effect=Exception("PERMISSION denied: privilege missing"))
        status = eg.check_system_tables_health(fn)
        assert status.sp_grants_valid is False
        assert status.missing_grants

    def test_warehouse_unreachable_exception(self):
        fn = MagicMock(side_effect=Exception("connection reset"))
        status = eg.check_system_tables_health(fn)
        assert status.warehouse_reachable is False
        assert "unreachable" in status.error

    def test_cache_hit_returns_without_recompute(self):
        fn = MagicMock(return_value=[{"1": 1}])
        first = eg.check_system_tables_health(fn)
        second = eg.check_system_tables_health(fn)
        assert first is second
        # Only computed once — second call served from cache
        assert fn.call_count == 1

    def test_get_health_status_dict(self):
        fn = MagicMock(return_value=[{"1": 1}])
        d = eg.get_health_status_dict(fn)
        assert isinstance(d, dict)
        assert d["system_tables_available"] is True


# ---------------------------------------------------------------------------
# C2 / C3 / C4 / C5 pure helpers
# ---------------------------------------------------------------------------

class TestDetectPartialCatalogAccess:
    def test_flags_inaccessible_catalogs(self):
        out = eg.detect_partial_catalog_access(["Main", "restricted"], ["main"])
        assert out == ["restricted"]

    def test_all_accessible(self):
        assert eg.detect_partial_catalog_access(["main"], ["main", "other"]) == []


class TestDetectForeignBoundaries:
    def test_by_type_and_by_flag(self):
        nodes = [
            {"name": "f1", "type": "FOREIGN", "catalog": "c"},
            {"name": "s1", "is_shared": True, "catalog": "c"},
            {"name": "normal", "type": "TABLE", "catalog": "c"},
        ]
        out = eg.detect_foreign_boundaries(nodes)
        assert "f1" in out and "s1" in out
        assert "normal" not in out


class TestApplyGraphTruncation:
    def test_no_truncation_when_small(self):
        nodes = [{"id": "a"}, {"id": "b"}]
        edges = [{"source": "a", "target": "b"}]
        n, e, trunc, reason = eg.apply_graph_truncation(nodes, edges, max_nodes=10)
        assert trunc is False and reason is None
        assert n == nodes and e == edges

    def test_truncates_and_filters_edges(self):
        nodes = [{"id": str(i)} for i in range(5)]
        edges = [{"source": "0", "target": "1"}, {"source": "3", "target": "4"}]
        n, e, trunc, reason = eg.apply_graph_truncation(nodes, edges, max_nodes=2)
        assert trunc is True
        assert len(n) == 2
        # edge (3->4) dropped since those nodes were truncated
        assert e == [{"source": "0", "target": "1"}]
        assert "truncated" in reason


class TestDetectOutsideLookback:
    def test_old_node_flagged_recent_ignored(self):
        nodes = [
            {"name": "old", "last_seen_at": "2000-01-01T00:00:00Z"},
            {"name": "recent", "last_seen_at": "2999-01-01T00:00:00Z"},
            {"name": "bad_ts", "last_seen_at": "not-a-date"},
            {"name": "no_ts"},
        ]
        out = eg.detect_outside_lookback(nodes, lookback_days=30)
        assert "old" in out
        assert "recent" not in out
        assert "bad_ts" not in out  # ValueError swallowed


# ---------------------------------------------------------------------------
# C7 / C11: ensure_feature_flags_table + check_capture_prerequisites
# ---------------------------------------------------------------------------

class TestEnsureFeatureFlagsTable:
    def test_table_exists(self):
        fn = MagicMock(return_value=[{"cnt": 3}])
        health = eg.ensure_feature_flags_table(fn)
        assert health.table_exists is True
        assert health.auto_created is False

    def test_auto_create_on_missing(self):
        calls = {"n": 0}

        def fn(sql):
            calls["n"] += 1
            if calls["n"] == 1:
                raise Exception("TABLE_OR_VIEW_NOT_FOUND: does not exist")
            return []  # CREATE succeeds

        health = eg.ensure_feature_flags_table(fn)
        assert health.auto_created is True
        assert health.table_exists is True

    def test_auto_create_failure(self):
        def fn(sql):
            if "CREATE TABLE" in sql:
                raise Exception("create denied")
            raise Exception("does not exist")

        health = eg.ensure_feature_flags_table(fn)
        assert health.table_accessible is False
        assert "Cannot create" in health.error

    def test_schema_not_found(self):
        fn = MagicMock(side_effect=Exception("schema xyz not found"))
        health = eg.ensure_feature_flags_table(fn)
        assert health.flags_schema_exists is False
        assert "setup.sql" in health.error

    def test_other_access_error(self):
        fn = MagicMock(side_effect=Exception("random failure"))
        health = eg.ensure_feature_flags_table(fn)
        assert health.table_accessible is False
        assert "Cannot access" in health.error


class TestCheckCapturePrerequisites:
    def test_all_ready(self):
        fn = MagicMock(return_value=[{"1": 1}])
        with patch("backend.feature_flags.get_flag_state", return_value=True):
            out = eg.check_capture_prerequisites(fn)
        assert out["schema_exists"] is True
        assert out["table_exists"] is True
        assert out["flag_enabled"] is True
        assert out["capture_ready"] is True
        assert out["issues"] == []

    def test_all_failing(self):
        fn = MagicMock(side_effect=Exception("nope"))
        with patch("backend.feature_flags.get_flag_state", side_effect=Exception("no flags")):
            out = eg.check_capture_prerequisites(fn)
        assert out["capture_ready"] is False
        assert len(out["issues"]) == 3


# ---------------------------------------------------------------------------
# C13: Spark Connect compatibility (static)
# ---------------------------------------------------------------------------

def test_get_spark_connect_compatibility():
    info = eg.get_spark_connect_compatibility()
    assert info["serverless_compatible"] is True
    assert "note" in info


# ---------------------------------------------------------------------------
# C14: soft_warm_cache
# ---------------------------------------------------------------------------

class _SyncThread:
    """Runs target synchronously so we can assert on warm side effects."""

    def __init__(self, target=None, daemon=None, name=None):
        self._target = target
        self.name = name or "sync-thread"

    def start(self):
        if self._target:
            self._target()


class TestSoftWarmCache:
    def test_warms_entries_into_cache(self):
        lineage_cache = {}
        fake_svc = MagicMock()
        fake_svc.get_recent_entries.return_value = [
            {"cache_key": "k1", "data": {"x": 1}},
            {"cache_key": "k2", "data": {"y": 2}},
        ]
        with patch("backend.cache_service.DeltaCacheService", return_value=fake_svc), \
             patch.object(eg.threading, "Thread", _SyncThread):
            out = eg.soft_warm_cache(MagicMock(), lineage_cache=lineage_cache)
        assert out["status"] == "warming"
        assert lineage_cache["k1"] == {"x": 1}
        assert lineage_cache["k2"] == {"y": 2}

    def test_warm_failure_is_non_fatal(self):
        with patch("backend.cache_service.DeltaCacheService", side_effect=Exception("boom")), \
             patch.object(eg.threading, "Thread", _SyncThread):
            out = eg.soft_warm_cache(MagicMock(), lineage_cache={})
        assert out["status"] == "warming"

    def test_already_started(self):
        eg._warm_started = True
        out = eg.soft_warm_cache(MagicMock())
        assert out["status"] == "already_started"


# ---------------------------------------------------------------------------
# C16: detect_scd_cdc_patterns
# ---------------------------------------------------------------------------

def _cols(names):
    return [{"col_name": n} for n in names]


class TestDetectScdCdcPatterns:
    def test_empty_columns_returns_default(self):
        fn = MagicMock(return_value=[])
        out = eg.detect_scd_cdc_patterns("c.s.t", fn)
        assert out["scd_detected"] is False

    def test_scd_type_2_high_confidence(self):
        fn = MagicMock(return_value=_cols(["id", "__start_at", "__end_at", "is_current"]))
        out = eg.detect_scd_cdc_patterns("c.s.t", fn)
        assert out["scd_detected"] is True
        assert out["pattern"] == "SCD_TYPE_2"
        assert out["confidence"] == "high"

    def test_cdc_feed(self):
        fn = MagicMock(return_value=_cols(["id", "_change_type", "_commit_version"]))
        out = eg.detect_scd_cdc_patterns("c.s.t", fn)
        assert out["cdc_detected"] is True
        assert out["pattern"] == "CDC_FEED"

    def test_scd_possible_single_indicator(self):
        fn = MagicMock(return_value=_cols(["id", "is_current"]))
        out = eg.detect_scd_cdc_patterns("c.s.t", fn)
        assert out["pattern"] == "SCD_POSSIBLE"
        assert out["confidence"] == "low"

    def test_exception_records_error(self):
        fn = MagicMock(side_effect=Exception("describe failed"))
        out = eg.detect_scd_cdc_patterns("c.s.t", fn)
        assert out["error"] == "describe failed"


# ---------------------------------------------------------------------------
# build_graph_warnings composite
# ---------------------------------------------------------------------------

class TestBuildGraphWarnings:
    def test_basic_warnings(self):
        nodes = [{"name": "n1", "type": "TABLE"}]
        w = eg.build_graph_warnings(
            nodes, [], accessible_catalogs=["main"], requested_catalogs=["main", "gone"]
        )
        assert w.node_count == 1
        assert "gone" in w.partial_catalog_access
        assert w.spark_connect_note

    def test_truncation_flag_when_at_max(self):
        nodes = [{"name": str(i)} for i in range(eg.MAX_GRAPH_NODES)]
        w = eg.build_graph_warnings(nodes, [])
        assert w.truncated is True
        assert w.truncation_reason


# ---------------------------------------------------------------------------
# run_startup_checks
# ---------------------------------------------------------------------------

class TestRunStartupChecks:
    def test_runs_all_and_logs_degraded(self):
        bad_health = eg.HealthStatus(
            system_tables_available=False, sp_grants_valid=False,
            missing_grants=["SELECT on x"],
        )
        ff_health = eg.FeatureFlagHealth(auto_created=True)
        with patch.object(eg, "check_system_tables_health", return_value=bad_health), \
             patch.object(eg, "ensure_feature_flags_table", return_value=ff_health), \
             patch.object(eg, "soft_warm_cache", return_value={"status": "warming"}):
            out = eg.run_startup_checks(MagicMock(), lineage_cache={})
        assert out["system_health"]["system_tables_available"] is False
        assert out["feature_flags"]["auto_created"] is True
        assert out["cache_warm"]["status"] == "warming"
        assert "spark_connect" in out
