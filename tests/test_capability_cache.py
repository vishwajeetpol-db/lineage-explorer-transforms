"""Tests for backend.server.capability_cache — per-table capability cache (v2.6.0).

The Impact / Root Cause / Governance / Access panels persist their last-computed
payload per (table, tab) in a Delta table and serve it instantly on reopen. This
covers:
- Singleton + graceful fallback on any SQL error (cache is optional, non-fatal)
- The `stale` flag (payload always served; only the badge changes past TTL)
- Eviction scopes: entry / table / all
- serve_or_compute read-through: cache hit vs miss, refresh bypass, `_cache` meta
"""
from unittest.mock import patch, MagicMock

import pytest


class TestCapabilityCacheSingleton:
    def test_returns_same_instance(self):
        from backend.server.capability_cache import get_capability_cache
        assert get_capability_cache() is get_capability_cache()

    def test_instance_type(self):
        from backend.server.capability_cache import get_capability_cache, CapabilityCache
        assert isinstance(get_capability_cache(), CapabilityCache)


class TestCapabilityCacheGetFallback:
    """get() must return None on any error — never raise (cache is optional)."""

    def test_get_returns_none_on_sql_error(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_sql", side_effect=RuntimeError("no warehouse")):
            assert svc.get("cat.sch.tbl", "impact") is None

    def test_get_returns_none_on_miss(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=[]):
            assert svc.get("cat.sch.tbl", "impact") is None

    def test_get_returns_payload_with_stale_flag(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        row = [{"value_json": '{"downstream_count": 3}', "cached_at": "2026-07-01T00:00:00Z",
                "cached_by": "a@b.com", "stale": True}]
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=row):
            hit = svc.get("cat.sch.tbl", "impact")
            assert hit is not None
            assert hit["data"] == {"downstream_count": 3}
            assert hit["stale"] is True
            assert hit["cached_by"] == "a@b.com"

    def test_get_none_when_value_json_null(self):
        """A vacuumed/evicted row (value_json NULL) is a miss."""
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        row = [{"value_json": None, "cached_at": "x", "cached_by": None, "stale": False}]
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=row):
            assert svc.get("cat.sch.tbl", "impact") is None


class TestCapabilityCacheSetFallback:
    def test_set_does_not_raise_on_error(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_sql", side_effect=RuntimeError("boom")):
            assert svc.set("cat.sch.tbl", "impact", {"x": 1}, actor="a@b.com") is False

    def test_set_rejects_oversized_payload(self):
        from backend.server.capability_cache import get_capability_cache, MAX_VALUE_BYTES
        svc = get_capability_cache()
        big = {"blob": "x" * (MAX_VALUE_BYTES + 10)}
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql") as mock_sql:
            assert svc.set("cat.sch.tbl", "impact", big, actor="a@b.com") is False
            mock_sql.assert_not_called()  # too big → never hits SQL

    def test_set_escapes_single_quotes(self):
        """A table name / payload with a quote must not break the MERGE."""
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=[]) as mock_sql:
            svc.set("cat.sch.o'brien", "impact", {"note": "it's fine"}, actor="a@b.com")
            sent = mock_sql.call_args[0][0]
            assert "o''brien" in sent  # escaped, not raw


class TestCapabilityCacheEviction:
    def test_evict_entry_issues_delete(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=[]) as mock_sql:
            assert svc.evict("cat.sch.tbl", "access") is True
            sent = mock_sql.call_args[0][0].upper()
            assert "DELETE FROM" in sent and "ACCESS" in mock_sql.call_args[0][0]

    def test_evict_table_returns_count(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_ensure_table"), \
             patch.object(svc, "_sql", side_effect=[[{"cnt": 3}], []]):
            assert svc.evict_table("cat.sch.tbl") == 3

    def test_evict_all_returns_count(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_ensure_table"), \
             patch.object(svc, "_sql", side_effect=[[{"cnt": 7}], []]):
            assert svc.evict_all() == 7

    def test_evict_fallback_returns_false_on_error(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        with patch.object(svc, "_sql", side_effect=RuntimeError("boom")):
            assert svc.evict("cat.sch.tbl", "impact") is False

    def test_inventory_shape(self):
        from backend.server.capability_cache import get_capability_cache
        svc = get_capability_cache()
        rows = [{"table_fqn": "c.s.t", "tab": "impact", "cached_at": "x",
                 "cached_by": "a@b.com", "stale": False}]
        with patch.object(svc, "_ensure_table"), patch.object(svc, "_sql", return_value=rows):
            inv = svc.inventory()
            assert len(inv) == 1
            assert inv[0]["tab"] == "impact"
            assert inv[0]["stale"] is False


class TestServeOrCompute:
    """The read-through helper the 4 capability routes use."""

    def test_cache_hit_returns_cached_and_skips_compute(self):
        from backend.server import capability_cache as cc
        compute = MagicMock(return_value={"live": True})
        hit = {"data": {"downstream_count": 5}, "cached_at": "t", "cached_by": "a@b.com", "stale": False}
        with patch.object(cc.get_capability_cache(), "get", return_value=hit):
            out = cc.serve_or_compute("c.s.t", "impact", compute, actor="a@b.com", refresh=False)
        compute.assert_not_called()
        assert out["downstream_count"] == 5
        assert out["_cache"]["from_cache"] is True
        assert out["_cache"]["stale"] is False

    def test_cache_miss_computes_and_stores(self):
        from backend.server import capability_cache as cc
        compute = MagicMock(return_value={"downstream_count": 9})
        svc = cc.get_capability_cache()
        with patch.object(svc, "get", return_value=None), \
             patch.object(svc, "set", return_value=True) as mock_set:
            out = cc.serve_or_compute("c.s.t", "impact", compute, actor="a@b.com", refresh=False)
        compute.assert_called_once()
        mock_set.assert_called_once()
        assert out["downstream_count"] == 9
        assert out["_cache"]["from_cache"] is False

    def test_refresh_bypasses_cache(self):
        from backend.server import capability_cache as cc
        compute = MagicMock(return_value={"downstream_count": 1})
        svc = cc.get_capability_cache()
        with patch.object(svc, "get") as mock_get, patch.object(svc, "set", return_value=True):
            out = cc.serve_or_compute("c.s.t", "impact", compute, actor="a@b.com", refresh=True)
        mock_get.assert_not_called()  # refresh never reads cache
        compute.assert_called_once()
        assert out["_cache"]["from_cache"] is False

    def test_non_dict_payload_passes_through_uncached(self):
        from backend.server import capability_cache as cc
        compute = MagicMock(return_value=["a", "list"])
        svc = cc.get_capability_cache()
        with patch.object(svc, "get", return_value=None), patch.object(svc, "set") as mock_set:
            out = cc.serve_or_compute("c.s.t", "impact", compute, actor="", refresh=False)
        assert out == ["a", "list"]
        mock_set.assert_not_called()
