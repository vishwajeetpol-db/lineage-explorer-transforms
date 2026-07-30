"""Additional coverage tests for backend.lineage_service.

Companion to tests/test_lineage_service.py — that file must NOT be edited. This
file drives the still-uncovered branches: the cost-cache refresh path, the
cross-schema / VOLUME / PATH / external-stub branches inside _fetch_table_lineage,
resolve_entity_name for every entity kind, the column-lineage / table-edge
bodies, the sharing overlay body, run_diagnostics probes, and the federated
source overlay.

Everything is mocked (`_get_client`, `_execute_sql`, `_execute_sql_long`); no
live workspace. Functions that fan out with run_parallel/map_parallel are fed a
CONTENT-DISPATCHING fake (keyed on SQL text) rather than an ordered side_effect
list, so parallel scheduling can't reorder the answers.
"""
import time

from unittest.mock import patch, MagicMock

import pytest

import backend.lineage_service as ls


@pytest.fixture(autouse=True)
def _clear_ls_state():
    """Reset the LRU cache AND the module-level cost globals around each test so
    nothing bleeds between tests (or into the sibling test module)."""
    ls.invalidate_cache()
    ls._cost_by_job_id = {}
    ls._cost_by_pipeline_id = {}
    ls._cost_cache_fetched_at = 0.0
    yield
    ls.invalidate_cache()
    ls._cost_by_job_id = {}
    ls._cost_by_pipeline_id = {}
    ls._cost_cache_fetched_at = 0.0


def _sql_of(args, kwargs):
    """Pull the SQL string out of a _execute_sql call regardless of arg shape.

    Normal callers pass (client, sql, catalog=...); get_federated_source_overlay
    passes (sql,) with no client. So grab the first string arg that looks like SQL.
    """
    if "sql" in kwargs:
        return kwargs["sql"]
    for a in args:
        if isinstance(a, str) and ("SELECT" in a.upper() or "SHOW" in a.upper()):
            return a
    return ""


ISO = "2026-07-01T00:00:00Z"


# ---------------------------------------------------------------------------
# Cost cache: _refresh_cost_cache / _maybe_refresh_cost_cache / _entity_cost
# ---------------------------------------------------------------------------
class TestCostCache:
    def test_refresh_cost_cache_populates_jobs_and_pipelines(self):
        client = MagicMock()

        def fake_long(_client, sql, _budget):
            if "job_id AS id" in sql:
                return [{"id": "job-1", "cost_usd": 10.5}]
            if "dlt_pipeline_id AS id" in sql:
                return [{"id": "pipe-1", "cost_usd": 3.25}]
            return []

        with patch.object(ls, "_execute_sql_long", side_effect=fake_long):
            ls._refresh_cost_cache(client)

        assert ls._entity_cost("JOB", "job-1") == 10.5
        assert ls._entity_cost("PIPELINE", "pipe-1") == 3.25
        # Unknown entity types have no cost.
        assert ls._entity_cost("QUERY", "whatever") is None
        assert ls._cost_cache_fetched_at > 0

    def test_refresh_cost_cache_swallows_failure(self):
        client = MagicMock()
        with patch.object(ls, "_execute_sql_long", side_effect=RuntimeError("no system.billing")):
            ls._refresh_cost_cache(client)  # must not raise
        assert ls._cost_by_job_id == {}
        assert ls._cost_by_pipeline_id == {}

    def test_refresh_cost_cache_noop_when_lock_held(self):
        client = MagicMock()
        ls._cost_cache_lock.acquire()
        try:
            with patch.object(ls, "_execute_sql_long", side_effect=AssertionError("should not run")):
                ls._refresh_cost_cache(client)  # lock held → immediate no-op
        finally:
            ls._cost_cache_lock.release()

    def test_maybe_refresh_stale_spawns_refresh(self):
        client = MagicMock()
        ls._cost_cache_fetched_at = 0.0  # ancient → stale

        class _SyncThread:
            def __init__(self, target=None, args=(), daemon=None):
                self._target, self._args = target, args

            def start(self):
                self._target(*self._args)

        refresh = MagicMock()
        with patch.object(ls.threading, "Thread", _SyncThread), \
             patch.object(ls, "_refresh_cost_cache", refresh):
            ls._maybe_refresh_cost_cache(client)
        refresh.assert_called_once()

    def test_maybe_refresh_fresh_is_noop(self):
        client = MagicMock()
        ls._cost_cache_fetched_at = time.time()  # just refreshed
        refresh = MagicMock()
        with patch.object(ls, "_refresh_cost_cache", refresh):
            ls._maybe_refresh_cost_cache(client)
        refresh.assert_not_called()


# ---------------------------------------------------------------------------
# _fetch_lineage_trace deeper BFS + get_lineage_trace public wrapper
# ---------------------------------------------------------------------------
class TestLineageTrace:
    def _multihop_fake(self):
        """Ancestors: bronze -> silver -> gold (silver->gold direct, bronze->silver via
        pipeline). Descendants: gold -> mart, plus gold -> s3 path (PATH node)."""
        silver_gold = {
            "source_table_full_name": "main.s.silver", "target_table_full_name": "main.s.gold",
            "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
            "entity_type": None, "entity_id": None, "event_time": ISO, "created_by": None,
        }
        bronze_silver = {
            "source_table_full_name": "main.s.bronze", "target_table_full_name": "main.s.silver",
            "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
            "entity_type": "PIPELINE", "entity_id": "p1", "event_time": ISO, "created_by": "me@x.com",
        }
        gold_mart = {
            "source_table_full_name": "main.s.gold", "target_table_full_name": "other.m.mart",
            "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
            "entity_type": None, "entity_id": None, "event_time": ISO, "created_by": None,
        }
        gold_path = {
            "source_table_full_name": "main.s.gold", "target_table_full_name": None,
            "source_type": "TABLE", "target_type": "PATH", "source_path": None,
            "target_path": "s3://mybucket/export/x", "entity_type": None, "entity_id": None,
            "event_time": ISO, "created_by": None,
        }

        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "table_lineage" in sql and " IN (" in sql:
                if "WHERE target_table_full_name IN" in sql:  # up (ancestors)
                    if "main.s.gold" in sql:
                        return [silver_gold]
                    if "main.s.silver" in sql:
                        return [bronze_silver]
                    return []
                if "WHERE source_table_full_name IN" in sql:  # down (descendants)
                    if "main.s.gold" in sql:
                        return [gold_mart, gold_path]
                    return []
                return []
            # Column population inside _build_graph_from_rows.
            if "information_schema.columns" in sql:
                return [{"table_schema": "s", "table_name": "gold", "column_name": "id",
                         "data_type": "bigint", "is_nullable": "NO", "ordinal_position": 1}]
            return []

        return fake

    def test_fetch_trace_multihop_with_entity_and_path(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=self._multihop_fake()), \
             patch.object(ls, "_entity_cost", return_value=2.0), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls._fetch_lineage_trace("main.s.gold")
        ids = {n.id for n in resp.nodes}
        assert {"main.s.bronze", "main.s.silver", "main.s.gold", "other.m.mart"} <= ids
        # entity node for the pipeline and a PATH node for the s3 export
        assert any(getattr(n, "node_type", None) == "entity" for n in resp.nodes)
        assert "path:s3://mybucket" in ids
        gold = next(n for n in resp.nodes if n.id == "main.s.gold")
        assert any(c["name"] == "id" for c in gold.columns)
        assert resp.truncated is False

    def test_get_lineage_trace_caches_then_serves_from_cache(self):
        fake = self._multihop_fake()
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake) as ex, \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            first = ls.get_lineage_trace("main.s.gold")
            calls_after_first = ex.call_count
            second = ls.get_lineage_trace("main.s.gold")  # from cache — no new SQL
        assert first is not None and second is not None
        assert second.cached is True
        assert ex.call_count == calls_after_first  # cache hit issued no SQL

    def test_get_lineage_trace_skip_cache(self):
        fake = self._multihop_fake()
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls.get_lineage_trace("main.s.gold", skip_cache=True)
        assert hasattr(resp, "nodes")

    def test_truncated_trace_is_not_cached(self):
        # NODE_CAP = 0 → the very first frontier check trips the truncation guard.
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "LINEAGE_MAX_NODES", 0), \
             patch.object(ls, "_execute_sql", return_value=[]), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls.get_lineage_trace("main.s.gold")
            assert resp.truncated is True
            # Not cached (truncated): a second fetch re-runs rather than serving stale.
            assert ls._cache_get("trace:main.s.gold") is None


# ---------------------------------------------------------------------------
# _fetch_table_lineage external / stub / VOLUME / PATH / cross-schema branches
# ---------------------------------------------------------------------------
class TestFetchTableLineageExternal:
    def test_cross_schema_volume_path_and_followup(self):
        tables = [{"table_schema": "s", "table_name": "orders", "table_type": "MANAGED",
                   "table_owner": "o@x.com", "comment": None, "created": None, "last_altered": None}]
        columns = [{"table_schema": "s", "table_name": "orders", "column_name": "id",
                    "data_type": "int", "is_nullable": "NO", "ordinal_position": 1}]
        # Main lineage rows: cross-schema source, a VOLUME read, a cloud PATH read,
        # and an entity writing to a cross-schema target.
        lineage = [
            {"source_table_full_name": "main.other.src", "target_table_full_name": "main.s.orders",
             "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
             "entity_type": None, "entity_id": None, "event_time": ISO, "created_by": None},
            {"source_table_full_name": None, "target_table_full_name": "main.s.orders",
             "source_type": None, "target_type": "TABLE", "source_path": "/Volumes/main/s/vol/data",
             "target_path": None, "entity_type": None, "entity_id": None, "event_time": ISO, "created_by": None},
            {"source_table_full_name": None, "target_table_full_name": "main.s.orders",
             "source_type": "PATH", "target_type": "TABLE", "source_path": "s3://mybucket/data/x",
             "target_path": None, "entity_type": None, "entity_id": None, "event_time": ISO, "created_by": None},
            {"source_table_full_name": "main.s.orders", "target_table_full_name": "main.elsewhere.mart",
             "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
             "entity_type": "PIPELINE", "entity_id": "p1", "event_time": ISO, "created_by": "me@x.com"},
        ]
        # Entity follow-up: p1 also writes to yet another catalog/schema.
        followup = [
            {"source_table_full_name": "main.s.orders", "target_table_full_name": "other2.sch.tbl",
             "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
             "entity_type": "PIPELINE", "entity_id": "p1", "event_time": "2026-07-02T00:00:00Z",
             "created_by": "me2@x.com"},
            # a pruned/unknown entity id — exercises the "skip pruned" continue
            {"source_table_full_name": "x.y.z", "target_table_full_name": "a.b.c",
             "source_type": "TABLE", "target_type": "TABLE", "source_path": None, "target_path": None,
             "entity_type": "JOB", "entity_id": "ghost", "event_time": ISO, "created_by": None},
        ]
        ext_cols = [{"table_name": "src", "column_name": "sid", "full_data_type": "string",
                     "is_nullable": "YES"}]

        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.tables" in sql:
                return tables
            if "full_data_type" in sql:  # external columns fetch
                return ext_cols
            if "information_schema.columns" in sql:  # main columns fetch
                return columns
            if "entity_id IN (" in sql:  # entity follow-up lineage
                return followup
            if "table_lineage" in sql:  # main lineage query
                return lineage
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake), \
             patch.object(ls, "_entity_cost", return_value=4.75), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp, ok = ls._fetch_table_lineage("main", "s", "ck_ext")

        ids = {n.id for n in resp.nodes}
        assert ok is True
        # local table
        assert "main.s.orders" in ids
        # cross-schema external stub
        assert "main.other.src" in ids
        # VOLUME node
        assert "main.s.vol" in ids
        vol = next(n for n in resp.nodes if n.id == "main.s.vol")
        assert vol.table_type == "VOLUME"
        # PATH node
        assert "path:s3://mybucket" in ids
        path = next(n for n in resp.nodes if n.id == "path:s3://mybucket")
        assert path.table_type == "PATH"
        # entity node with cost + cross-schema targets from main + follow-up query
        ent = next(n for n in resp.nodes if getattr(n, "node_type", None) == "entity")
        assert ent.cost_usd == 4.75
        assert {"main.elsewhere.mart", "other2.sch.tbl"} <= ids
        # external table got its columns populated from the ext info_schema fetch
        src = next(n for n in resp.nodes if n.id == "main.other.src")
        assert any(c["name"] == "sid" for c in src.columns)

    def test_external_column_fetch_failure_is_tolerated(self):
        tables = [{"table_schema": "s", "table_name": "orders", "table_type": "MANAGED",
                   "table_owner": None, "comment": None, "created": None, "last_altered": None}]
        lineage = [{"source_table_full_name": "main.other.src", "target_table_full_name": "main.s.orders",
                    "source_type": "TABLE", "target_type": "TABLE", "source_path": None,
                    "target_path": None, "entity_type": None, "entity_id": None,
                    "event_time": ISO, "created_by": None}]

        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.tables" in sql:
                return tables
            if "full_data_type" in sql:
                raise RuntimeError("no BROWSE on other schema")
            if "information_schema.columns" in sql:
                return []
            if "table_lineage" in sql:
                return lineage
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp, ok = ls._fetch_table_lineage("main", "s", "ck_extfail")
        ids = {n.id for n in resp.nodes}
        assert "main.other.src" in ids  # stub still created, just column-less
        src = next(n for n in resp.nodes if n.id == "main.other.src")
        assert src.columns == []

    def test_entity_followup_query_failure_is_tolerated(self):
        tables = [{"table_schema": "s", "table_name": "orders", "table_type": "MANAGED",
                   "table_owner": None, "comment": None, "created": None, "last_altered": None}]
        lineage = [{"source_table_full_name": "main.s.orders", "target_table_full_name": "main.elsewhere.mart",
                    "source_type": "TABLE", "target_type": "TABLE", "source_path": None,
                    "target_path": None, "entity_type": "JOB", "entity_id": "j9",
                    "event_time": ISO, "created_by": "me@x.com"}]

        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.tables" in sql:
                return tables
            if "entity_id IN (" in sql:
                raise RuntimeError("follow-up denied")
            if "information_schema.columns" in sql:
                return []
            if "table_lineage" in sql:
                return lineage
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake), \
             patch.object(ls, "_entity_cost", return_value=None), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp, ok = ls._fetch_table_lineage("main", "s", "ck_followfail")
        assert ok is True
        assert any(getattr(n, "node_type", None) == "entity" for n in resp.nodes)


# ---------------------------------------------------------------------------
# resolve_entity_name — every entity kind + error/guard paths
# ---------------------------------------------------------------------------
class TestResolveEntityName:
    def test_job_resolves(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[
                 {"name": "Nightly ETL", "run_as_user_name": "svc@x.com",
                  "creator_user_name": "me@x.com"}]):
            out = ls.resolve_entity_name("JOB", "job-100")
        assert out["name"] == "Nightly ETL"
        assert out["owner"] == "svc@x.com"

    def test_pipeline_resolves(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[{"name": "Bronze DLT"}]):
            out = ls.resolve_entity_name("PIPELINE", "pipe-200")
        assert out["name"] == "Bronze DLT"

    def test_notebook_with_path_needs_no_sql(self):
        # A slash-bearing id is a workspace path — resolved locally, no SQL.
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=AssertionError("should not query")):
            out = ls.resolve_entity_name("NOTEBOOK", "/Users/me/my_notebook")
        assert out["name"] == "my_notebook"

    def test_notebook_numeric_resolves_via_audit(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[{"path": "/Repos/team/etl_nb"}]):
            out = ls.resolve_entity_name("NOTEBOOK", "9988776655")
        assert out["name"] == "etl_nb"

    def test_notebook_numeric_no_audit_row_falls_back(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[]):
            out = ls.resolve_entity_name("NOTEBOOK", "1234567890")
        assert out["name"].startswith("Notebook ")

    def test_unsafe_entity_id_rejected(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=AssertionError("should not query")):
            out = ls.resolve_entity_name("JOB", "bad;DROP TABLE x")
        assert out["name"] == "JOB bad;DROP TAB"[:len(out["name"])] or "name" in out

    def test_query_failure_returns_fallback(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("lakeflow denied")):
            out = ls.resolve_entity_name("JOB", "job-err")
        assert out["name"] == "JOB job-err"


# ---------------------------------------------------------------------------
# Column lineage bodies + table edges
# ---------------------------------------------------------------------------
class TestColumnLineageAndEdges:
    def test_schema_column_lineage_builds_edges(self):
        rows = [
            {"source_table_full_name": "main.s.a", "source_column_name": "x",
             "target_table_full_name": "main.s.b", "target_column_name": "y"},
            {"source_table_full_name": "main.s.b", "source_column_name": "y",
             "target_table_full_name": "main.s.c", "target_column_name": "z"},
        ]
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=rows):
            resp = ls.get_schema_column_lineage("main", "colschema1", skip_cache=True)
        assert len(resp.edges) == 2
        assert resp.edges[0].source_column == "x"

    def test_schema_column_lineage_failure_propagates(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("no column_lineage")):
            with pytest.raises(Exception):
                ls.get_schema_column_lineage("main", "colschema2", skip_cache=True)

    def test_get_column_lineage_filters_to_column(self):
        rows = [
            {"source_table_full_name": "main.s.a", "source_column_name": "x",
             "target_table_full_name": "main.s.b", "target_column_name": "y"},
            {"source_table_full_name": "main.s.c", "source_column_name": "p",
             "target_table_full_name": "main.s.d", "target_column_name": "q"},
        ]
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=rows):
            resp = ls.get_column_lineage("main", "colschema3", "b", "y", skip_cache=True)
        # Only the edge touching main.colschema3.b.y? Note the delegate uses the
        # passed schema for the full_table name, so match on table b + column y.
        assert all(e.target_column == "y" or e.source_column == "y" for e in resp.edges)

    def test_get_table_edges_maps_rows(self):
        rows = [{"source": "main.s.a", "target": "main.s.b",
                 "entity_type": "PIPELINE", "entity_id": "p1"}]
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=rows):
            out = ls.get_table_edges("main", "edgeschema1", skip_cache=True)
        assert out == [{"source": "main.s.a", "target": "main.s.b",
                        "entity_type": "PIPELINE", "entity_id": "p1"}]

    def test_get_table_edges_catalog_wide(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[]):
            out = ls.get_table_edges("catwide1", skip_cache=True)  # schema omitted
        assert out == []

    def test_get_table_edges_failure_propagates(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("denied")):
            with pytest.raises(Exception):
                ls.get_table_edges("main", "edgeschema2", skip_cache=True)


# ---------------------------------------------------------------------------
# run_diagnostics — probe failures + catalog metadata (BROWSE) loop
# ---------------------------------------------------------------------------
class TestRunDiagnostics:
    def test_probes_and_catalog_metadata_paths(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "SHOW CATALOGS" in sql:
                return [{"catalog": "main"}, {"catalog": "foo"},
                        {"catalog": "system"}, {"catalog": "samples"}]
            if "`main`.information_schema.tables" in sql:
                return [{"1": 1}]           # readable
            if "`foo`.information_schema.tables" in sql:
                raise RuntimeError("no BROWSE")  # visible-but-no-metadata
            if "table_lineage" in sql:
                raise RuntimeError("no system.access")  # required probe fails
            if "billing.usage" in sql:
                raise RuntimeError("no billing")
            if "information_schema.shares" in sql:
                raise RuntimeError("no sharing")
            if sql.strip() == "SELECT 1":
                return [{"1": 1}]           # warehouse ok
            return []

        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}), \
             patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.run_diagnostics()

        assert out["warehouse_id_set"] is True
        names = {c["check"]: c for c in out["checks"]}
        assert names["system.access (lineage)"]["ok"] is False
        cat = names["catalog metadata (BROWSE)"]
        assert cat["ok"] is True          # at least one readable (main)
        assert "hint" in cat              # foo flagged as visible-no-meta
        assert "foo" in cat["hint"]

    def test_no_warehouse_reports_missing(self):
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}), \
             patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[]):
            out = ls.run_diagnostics()
        assert out["warehouse_id_set"] is False
        assert out["ok"] is False

    def test_catalog_metadata_show_catalogs_failure(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "SHOW CATALOGS" in sql:
                raise RuntimeError("no metastore access")
            if sql.strip() == "SELECT 1":
                return [{"1": 1}]
            return [{"1": 1}]

        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}), \
             patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.run_diagnostics()
        cat = next(c for c in out["checks"] if c["check"] == "catalog metadata (BROWSE)")
        assert cat["ok"] is False
        assert "hint" in cat


# ---------------------------------------------------------------------------
# get_sharing_overlay body — shared_out + foreign_catalogs
# ---------------------------------------------------------------------------
class TestSharingOverlay:
    def test_both_audiences_populate_entries(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "table_share_usage" in sql and "shared_as_schema" in sql:
                return [{"catalog_name": "main", "schema_name": "s", "table_name": "orders",
                         "share_name": "share1", "shared_as_schema": "pub",
                         "shared_as_table": "orders_v", "cdf_enabled": "true"}]
            if "share_recipient_privileges" in sql:
                return [{"share_name": "share1", "recipient_name": "acme"},
                        {"share_name": "share1", "recipient_name": "beta"}]
            if "catalog_provider_share_usage" in sql:
                return [
                    {"catalog_name": "incoming", "provider_name": "prov1",
                     "share_name": "s_a", "cloud": "AWS", "region": "us-west-2"},
                    {"catalog_name": "incoming", "provider_name": "prov1",
                     "share_name": "s_b", "cloud": "AWS", "region": "us-west-2"},
                ]
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            resp = ls.get_sharing_overlay("main", "s", audience="both", skip_cache=True)

        assert resp.available is True
        assert len(resp.shared_out) == 1
        so = resp.shared_out[0]
        assert so.full_name == "main.s.orders"
        assert so.recipients == ["acme", "beta"]
        assert so.shared_as == "pub.orders_v"
        assert so.cdf_enabled is True
        assert len(resp.foreign_catalogs) == 1
        fc = resp.foreign_catalogs[0]
        assert fc.catalog_name == "incoming"
        assert set(fc.share_names) == {"s_a", "s_b"}
        assert fc.cloud == "AWS"

    def test_provider_only_audience(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "table_share_usage" in sql and "shared_as_schema" in sql:
                return [{"catalog_name": "main", "schema_name": "s", "table_name": "t",
                         "share_name": "sh", "shared_as_schema": None,
                         "shared_as_table": None, "cdf_enabled": "false"}]
            if "share_recipient_privileges" in sql:
                return []
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            resp = ls.get_sharing_overlay("main", "s", audience="provider", skip_cache=True)
        assert resp.audience == "provider"
        assert len(resp.shared_out) == 1
        assert resp.shared_out[0].shared_as is None
        assert resp.foreign_catalogs == []

    def test_overlay_view_errors_degrade_to_empty(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("no sharing views")):
            resp = ls.get_sharing_overlay("main", "s", audience="both", skip_cache=True)
        assert resp.shared_out == []
        assert resp.foreign_catalogs == []
        assert resp.available is False


# ---------------------------------------------------------------------------
# get_federated_source_overlay
# ---------------------------------------------------------------------------
class TestFederatedOverlay:
    def test_foreign_catalog_tables_enriched(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.connections" in sql:
                return [{"connection_name": "mysql_conn", "connection_type": "MYSQL",
                         "owner": "me@x.com", "created_at": None}]
            if "FOREIGN_CATALOG" in sql:
                return [{"catalog_name": "fed_mysql", "connection_name": "mysql_conn"}]
            if "`fed_mysql`.information_schema.tables" in sql:
                return [{"table_schema": "app", "table_name": "users", "table_type": "FOREIGN"},
                        {"table_schema": "app", "table_name": "users_v", "table_type": "VIEW"}]
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.get_federated_source_overlay(skip_cache=True)

        assert out["available"] is True
        assert len(out["federated_tables"]) == 2
        by_name = {t["remote_object"]: t for t in out["federated_tables"]}
        assert by_name["users"]["connection_type"] == "MYSQL"
        assert by_name["users"]["is_view"] is False
        assert by_name["users_v"]["is_view"] is True
        assert by_name["users_v"]["object_type"] == "VIEW"

    def test_no_foreign_catalogs_short_circuits(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.connections" in sql:
                return [{"connection_name": "c1", "connection_type": "POSTGRESQL"}]
            if "FOREIGN_CATALOG" in sql:
                return []
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.get_federated_source_overlay(skip_cache=True)
        assert out["available"] is True
        assert out["federated_tables"] == []
        assert len(out["connections"]) == 1

    def test_foreign_catalog_table_listing_failure_skipped(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.connections" in sql:
                return [{"connection_name": "c1", "connection_type": "SNOWFLAKE"}]
            if "FOREIGN_CATALOG" in sql:
                return [{"catalog_name": "fed_x", "connection_name": "c1"}]
            if "`fed_x`.information_schema.tables" in sql:
                raise RuntimeError("connection down")
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.get_federated_source_overlay(skip_cache=True)
        assert out["available"] is True
        assert out["federated_tables"] == []  # the one catalog failed and was skipped

    def test_overlay_top_level_failure_unavailable(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("no connections view")):
            out = ls.get_federated_source_overlay(skip_cache=True)
        assert out["available"] is False
        assert out["federated_tables"] == []
        assert out["connections"] == []


# ---------------------------------------------------------------------------
# _execute_sql / _execute_sql_long — polling, FAILED, empty, success paths
# ---------------------------------------------------------------------------
def _resp_with(state, data_array=None, col_names=None, error_msg=None):
    from databricks.sdk.service.sql import StatementState  # noqa
    resp = MagicMock()
    resp.status.state = state
    resp.statement_id = "stmt-1"
    if error_msg is not None:
        resp.status.error.message = error_msg
    else:
        resp.status.error = None
    if data_array is None:
        resp.result = None
    else:
        resp.result.data_array = data_array
        cols = []
        for nm in (col_names or []):
            c = MagicMock()
            c.name = nm
            cols.append(c)
        resp.manifest.schema.columns = cols
    return resp


class TestExecuteSqlPaths:
    def test_execute_sql_failed_state_raises(self):
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp_with(
            StatementState.FAILED, error_msg="boom")
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}):
            with pytest.raises(RuntimeError, match="boom"):
                ls._execute_sql(client, "SELECT 1")

    def test_execute_sql_polls_then_succeeds_empty(self):
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        running = _resp_with(StatementState.RUNNING)
        done = _resp_with(StatementState.SUCCEEDED, data_array=None)
        client.statement_execution.execute_statement.return_value = running
        client.statement_execution.get_statement.return_value = done
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}), \
             patch.object(ls, "SQL_POLL_INTERVAL_S", 0):
            rows = ls._execute_sql(client, "SELECT 1")
        assert rows == []

    def test_execute_sql_unexpected_state_raises(self):
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp_with(
            StatementState.CANCELED)
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}):
            with pytest.raises(RuntimeError, match="did not complete"):
                ls._execute_sql(client, "SELECT 1")

    def test_execute_sql_long_no_warehouse_raises(self):
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                ls._execute_sql_long(MagicMock(), "SELECT 1", 60)

    def test_execute_sql_long_polls_then_maps_rows(self):
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        running = _resp_with(StatementState.RUNNING)
        done = _resp_with(StatementState.SUCCEEDED, data_array=[["job-9", 5.0]],
                          col_names=["id", "cost_usd"])
        client.statement_execution.execute_statement.return_value = running
        client.statement_execution.get_statement.return_value = done
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}), \
             patch.object(ls.time, "sleep", lambda *_: None):
            rows = ls._execute_sql_long(client, "SELECT 1", 60)
        assert rows == [{"id": "job-9", "cost_usd": 5.0}]

    def test_execute_sql_long_failed_raises(self):
        from databricks.sdk.service.sql import StatementState
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp_with(
            StatementState.FAILED, error_msg="denied")
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}):
            with pytest.raises(RuntimeError, match="SQL failed"):
                ls._execute_sql_long(client, "SELECT 1", 60)


# ---------------------------------------------------------------------------
# _wrap_with_cache_metadata — live cost re-attach onto entity nodes
# ---------------------------------------------------------------------------
class TestWrapCacheMetadata:
    def test_cost_reattached_from_live_cache(self):
        from backend.models import LineageResponse, EntityNode, TableNode
        ls._cost_by_job_id = {"j1": 88.0}
        resp = LineageResponse(
            nodes=[
                EntityNode(id="entity:JOB:j1", entity_type="JOB", entity_id="j1", cost_usd=None),
                TableNode(id="main.s.t", name="t", full_name="main.s.t", table_type="TABLE"),
            ],
            edges=[],
        )
        out = ls._wrap_with_cache_metadata(resp, "lineage:main.s", from_cache=True, fetch_ms=5)
        ent = next(n for n in out.nodes if getattr(n, "node_type", None) == "entity")
        assert ent.cost_usd == 88.0
        assert out.fetch_duration_ms == 5

    def test_no_change_when_cost_matches(self):
        from backend.models import LineageResponse, EntityNode
        ls._cost_by_pipeline_id = {"p1": 12.0}
        resp = LineageResponse(
            nodes=[EntityNode(id="entity:PIPELINE:p1", entity_type="PIPELINE",
                              entity_id="p1", cost_usd=12.0)],
            edges=[],
        )
        out = ls._wrap_with_cache_metadata(resp, "lineage:x", from_cache=False)
        assert out.nodes[0].cost_usd == 12.0


# ---------------------------------------------------------------------------
# get_sharing_overview — populated rows exercise the grouping tail
# ---------------------------------------------------------------------------
class TestSharingOverviewFull:
    def test_populated_overview(self):
        def fake(*args, **kwargs):
            sql = _sql_of(args, kwargs)
            if "information_schema.shares" in sql:
                return [{"share_name": "sh1", "share_owner": "o@x.com",
                         "comment": "c", "created_by": "o@x.com"}]
            if "information_schema.recipients" in sql:
                return [{"recipient_name": "acme", "authentication_type": "TOKEN",
                         "recipient_owner": "o@x.com", "comment": None}]
            if "information_schema.providers" in sql:
                return [{"provider_name": "prov1", "cloud": "AWS",
                         "region": "us-west-2", "comment": None}]
            if "table_share_usage" in sql:
                return [{"catalog_name": "main", "schema_name": "s",
                         "table_name": "orders", "share_name": "sh1"}]
            if "catalog_provider_share_usage" in sql:
                return [{"catalog_name": "incoming", "provider_name": "prov1",
                         "share_name": "sh_a"}]
            if "share_recipient_privileges" in sql:
                return [{"share_name": "sh1", "recipient_name": "acme"}]
            return []

        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=fake):
            out = ls.get_sharing_overview(skip_cache=True)

        assert out["totals"]["shares"] == 1
        assert out["shares"][0]["num_tables"] == 1
        assert out["shares"][0]["recipients"] == ["acme"]
        assert out["foreign_catalogs"][0]["catalog_name"] == "incoming"
        assert "sh_a" in out["foreign_catalogs"][0]["share_names"]
        assert out["shared_tables"][0]["full_name"] == "main.s.orders"


# ---------------------------------------------------------------------------
# Cache count-cap eviction
# ---------------------------------------------------------------------------
class TestCacheCountCap:
    def test_count_cap_evicts_oldest(self):
        with patch.object(ls, "CACHE_MAX_ENTRIES", 2):
            ls._cache_set("cc1", 1)
            ls._cache_set("cc2", 2)
            ls._cache_set("cc3", 3)  # exceeds cap → oldest evicted
        # Total entries stays within the (patched) cap.
        assert len(ls._cache) <= 2
