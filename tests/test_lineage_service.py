"""Tests for backend.lineage_service — the core lineage/query/caching engine.

Everything is mocked (`_get_client`, `_execute_sql`); no live workspace. The
conftest autouse fixture calls invalidate_cache() after each test, so the
module-level LRU doesn't bleed between tests.
"""
from unittest.mock import patch, MagicMock

import pytest

import backend.lineage_service as ls


@pytest.fixture(autouse=True)
def _clear_ls_cache():
    ls.invalidate_cache()
    yield
    ls.invalidate_cache()


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------
class TestCacheHelpers:
    def test_set_get_roundtrip(self):
        ls._cache_set("k1", {"a": 1})
        assert ls._cache_get("k1") == {"a": 1}

    def test_get_miss_returns_none(self):
        assert ls._cache_get("nope-missing") is None

    def test_invalidate_prefix(self):
        ls._cache_set("lineage:x", 1)
        ls._cache_set("catalogs", 2)
        ls.invalidate_cache(prefix="lineage:")
        assert ls._cache_get("lineage:x") is None
        assert ls._cache_get("catalogs") == 2

    def test_invalidate_all(self):
        ls._cache_set("a", 1)
        ls.invalidate_cache()
        assert ls._cache_get("a") is None

    def test_evict_cache_entry(self):
        ls._cache_set("evict-me", 1)
        assert ls.evict_cache_entry("evict-me") is True
        assert ls._cache_get("evict-me") is None

    def test_evict_missing_returns_false(self):
        assert ls.evict_cache_entry("never-existed") is False

    def test_cached_fetch_calls_once_then_caches(self):
        calls = []
        def fetch():
            calls.append(1)
            return "value"
        r1 = ls._cached_fetch("cf-key", fetch)
        r2 = ls._cached_fetch("cf-key", fetch)
        assert r1 == r2 == "value"
        assert len(calls) == 1  # second call served from cache

    def test_cached_fetch_skip_cache(self):
        calls = []
        ls._cached_fetch("cf2", lambda: calls.append(1) or "x")
        ls._cached_fetch("cf2", lambda: calls.append(1) or "x", skip_cache=True)
        assert len(calls) == 2

    def test_get_cache_snapshot_shape(self):
        ls._cache_set("snap", {"x": 1})
        entries, total, inflight = ls.get_cache_snapshot()
        assert isinstance(entries, list)
        assert isinstance(total, int)
        assert isinstance(inflight, list)

    def test_estimate_value_size(self):
        assert ls._estimate_value_size({"a": [1, 2, 3]}) > 0

    def test_keyed_lock_is_stable(self):
        assert ls._get_keyed_lock("same") is ls._get_keyed_lock("same")


# ---------------------------------------------------------------------------
# Listing functions (SHOW CATALOGS / SCHEMAS / information_schema.tables)
# ---------------------------------------------------------------------------
class TestListings:
    def test_list_catalogs_filters_system(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[
                 {"catalog": "main"}, {"catalog": "system"}, {"catalog": "analytics"}]):
            out = ls.list_catalogs()
        assert "system" not in out
        assert set(out) == {"main", "analytics"}

    def test_list_catalogs_error_returns_empty(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("no warehouse")):
            assert ls.list_catalogs() == []

    def test_list_schemas_filters_and_sorts(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[
                 {"databaseName": "z"}, {"databaseName": "information_schema"},
                 {"databaseName": "a"}]):
            out = ls.list_schemas("main")
        assert out == ["a", "z"]

    def test_list_schemas_error_returns_empty(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("x")):
            assert ls.list_schemas("main") == []

    def test_list_all_tables_aggregates_catalogs(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "list_catalogs", return_value=["main"]), \
             patch.object(ls, "_execute_sql", return_value=[
                 {"table_name": "orders", "table_type": "MANAGED", "table_schema": "sales"}]):
            out = ls.list_all_tables()
        assert len(out) == 1
        assert out[0]["fqdn"] == "main.sales.orders"
        assert out[0]["catalog"] == "main"

    def test_list_all_tables_catalog_error_skipped(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "list_catalogs", return_value=["bad"]), \
             patch.object(ls, "_execute_sql", side_effect=RuntimeError("no browse")):
            assert ls.list_all_tables() == []


# ---------------------------------------------------------------------------
# _execute_sql behavior
# ---------------------------------------------------------------------------
class TestExecuteSql:
    def test_no_warehouse_raises(self):
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": ""}):
            with pytest.raises(Exception):
                ls._execute_sql(MagicMock(), "SELECT 1")

    def test_success_maps_rows(self):
        client = MagicMock()
        resp = client.statement_execution.execute_statement.return_value
        from databricks.sdk.service.sql import StatementState
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["main"]]
        col = MagicMock(); col.name = "catalog"
        resp.manifest.schema.columns = [col]
        with patch.dict("os.environ", {"DATABRICKS_WAREHOUSE_ID": "wh-1"}):
            rows = ls._execute_sql(client, "SHOW CATALOGS")
        assert rows == [{"catalog": "main"}]


# ---------------------------------------------------------------------------
# Graph building + classification
# ---------------------------------------------------------------------------
class TestGraphBuild:
    def test_build_graph_from_empty_rows(self):
        client = MagicMock()
        resp = ls._build_graph_from_rows(client, [])
        assert resp.nodes == [] and resp.edges == []

    def test_internal_lineage_filter_is_sql(self):
        f = ls._internal_lineage_filter()
        assert isinstance(f, str)

    def test_build_graph_table_to_table_direct(self):
        client = MagicMock()
        rows = [{
            "source_table_full_name": "main.s.a", "source_type": "TABLE",
            "target_table_full_name": "main.s.b", "target_type": "TABLE",
            "entity_type": None, "entity_id": None,
        }]
        with patch.object(ls, "_execute_sql", return_value=[]), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls._build_graph_from_rows(client, rows)
        ids = {n.id for n in resp.nodes}
        assert {"main.s.a", "main.s.b"} <= ids
        assert any(e.source == "main.s.a" and e.target == "main.s.b" for e in resp.edges)

    def test_build_graph_with_entity_node_and_edges(self):
        client = MagicMock()
        rows = [{
            "source_table_full_name": "main.s.src", "source_type": "TABLE",
            "target_table_full_name": "main.s.out", "target_type": "TABLE",
            "entity_type": "PIPELINE", "entity_id": "p1",
            "event_time": "2026-07-01T00:00:00Z", "created_by": "me@x.com",
        }]
        with patch.object(ls, "_execute_sql", return_value=[]), \
             patch.object(ls, "_entity_cost", return_value=1.25), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls._build_graph_from_rows(client, rows)
        ent = [n for n in resp.nodes if getattr(n, "node_type", None) == "entity"]
        assert ent and ent[0].entity_id == "p1"
        assert ent[0].cost_usd == 1.25
        # src -> entity and entity -> out
        pairs = {(e.source, e.target) for e in resp.edges}
        assert ("main.s.src", "entity:PIPELINE:p1") in pairs
        assert ("entity:PIPELINE:p1", "main.s.out") in pairs

    def test_build_graph_read_after_write_no_back_edge(self):
        """A table the entity WRITES then reads back becomes a direct table edge."""
        client = MagicMock()
        rows = [
            {"source_table_full_name": "main.s.raw", "target_table_full_name": "main.s.mid",
             "entity_type": "PIPELINE", "entity_id": "p1"},
            {"source_table_full_name": "main.s.mid", "target_table_full_name": "main.s.final",
             "entity_type": "PIPELINE", "entity_id": "p1"},
        ]
        with patch.object(ls, "_execute_sql", return_value=[]), \
             patch.object(ls, "_entity_cost", return_value=None), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls._build_graph_from_rows(client, rows)
        pairs = {(e.source, e.target) for e in resp.edges}
        # mid is written by p1, so its read-back is a direct mid->final edge,
        # not a back-edge mid->entity.
        assert ("main.s.mid", "entity:PIPELINE:p1") not in pairs
        assert ("main.s.mid", "main.s.final") in pairs

    def test_build_graph_populates_columns(self):
        client = MagicMock()
        rows = [{"source_table_full_name": "main.s.a", "target_table_full_name": "main.s.b"}]
        colrows = [
            {"table_schema": "s", "table_name": "a", "column_name": "id",
             "data_type": "int", "is_nullable": "NO", "ordinal_position": 1},
        ]
        with patch.object(ls, "_execute_sql", return_value=colrows), \
             patch.object(ls, "_maybe_refresh_cost_cache"):
            resp = ls._build_graph_from_rows(client, rows)
        a = next(n for n in resp.nodes if n.id == "main.s.a")
        assert any(c["name"] == "id" for c in a.columns)

    def test_parse_lineage_ref_table_and_path(self):
        ref, t = ls._parse_lineage_ref("main.s.t", None, "TABLE")
        assert ref == "main.s.t" and t == "TABLE"
        ref2, t2 = ls._parse_lineage_ref(None, "s3://bucket/x", "PATH")
        assert ref2 is not None


# ---------------------------------------------------------------------------
# Public lineage entry points (thin wrappers over cached fetchers)
# ---------------------------------------------------------------------------
class TestPublicLineage:
    def test_get_table_lineage_wraps_response(self):
        from backend.models import LineageResponse
        with patch.object(ls, "_fetch_table_lineage",
                          return_value=(LineageResponse(nodes=[], edges=[]), False)):
            r = ls.get_table_lineage("main", "default")
        assert hasattr(r, "nodes")

    def test_get_columns_ok(self):
        with patch.object(ls, "_get_client", return_value=MagicMock()), \
             patch.object(ls, "_execute_sql", return_value=[
                 {"column_name": "id", "data_type": "int", "is_nullable": "YES",
                  "ordinal_position": 1}]):
            out = ls.get_columns("main", "default", "orders")
        assert isinstance(out, list) and out[0]["name"] == "id"

    def test_resolve_entity_name_delegates(self):
        with patch("backend.server.entities.resolve_entity",
                   return_value={"name": "My Job", "deep_link": None}) if _importable_entities() else _noop():
            out = ls.resolve_entity_name("JOB", "123")
        assert isinstance(out, dict)


def _importable_entities():
    try:
        import backend.server.entities  # noqa
        return True
    except Exception:
        return False


import contextlib


@contextlib.contextmanager
def _noop():
    yield
