"""Full-path coverage for backend.cache_service.DeltaCacheService.

The existing test_cache_service.py covers fallbacks; this drives the SQL-success
paths (get hit, set MERGE, invalidate, vacuum, get_recent_entries, stats) by
mocking the DeltaCacheService._sql method to return crafted rows.
"""
import json
from unittest.mock import patch

from backend.cache_service import get_cache_service, DeltaCacheService, MAX_VALUE_BYTES


def _svc():
    s = DeltaCacheService()
    s._ready = True  # skip _ensure_table
    return s


class TestGet:
    def test_hit_deserializes(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"value_json": json.dumps({"a": 1})}], []]):
            assert s.get("k", "ns") == {"a": 1}

    def test_miss_returns_none(self):
        s = _svc()
        with patch.object(s, "_sql", return_value=[]):
            assert s.get("k", "ns") is None


class TestSet:
    def test_merge_executes(self):
        s = _svc()
        with patch.object(s, "_sql", return_value=[]) as m:
            assert s.set("k", {"a": 1}, ttl_seconds=60, namespace="ns") is True
            assert "MERGE INTO" in m.call_args[0][0]

    def test_oversized_rejected(self):
        s = _svc()
        with patch.object(s, "_sql") as m:
            assert s.set("k", "x" * (MAX_VALUE_BYTES + 1), namespace="ns") is False
            m.assert_not_called()

    def test_payload_backslash_quote_escaped(self):
        r"""A payload value starting `\'` must not be able to close the literal.
        Quote-doubling alone produced `\''`, whose first quote Spark treats as an
        escaped quote and whose second CLOSES the string. json.dumps already
        doubles the backslash, and sql_str doubles it again — four in the
        statement — so nothing here is left for Spark to consume."""
        s = _svc()
        with patch.object(s, "_sql", return_value=[]) as m:
            assert s.set("k", "\\' OR 1=1--", namespace="ns") is True
            sent = m.call_args[0][0]
            assert "\\\\\\\\'' OR 1=1--" in sent


class TestNamespaceEscaping:
    """The namespace lands in the cache_ns literal of every statement."""

    def test_namespace_escaped_not_sliced_after_escaping(self):
        r"""sql_str(limit=) truncates BEFORE escaping. Slicing the escaped form
        could cut a `\\` or `''` pair in half and re-open the literal."""
        s = _svc()
        long_ns = "\\'" + ("a" * 200)
        with patch.object(s, "_sql", return_value=[]) as m:
            s.invalidate("k", long_ns)
            sent = m.call_args[0][0]
            assert "'\\\\''" in sent            # backslash doubled, then quote
            assert "'\\''" not in sent          # bypassable form absent
            # Cap is 100 SOURCE chars: the 2-char prefix + 98 'a's, and the
            # doubling that follows never lands mid-pair.
            assert "a" * 98 in sent
            assert "a" * 99 not in sent


class TestInvalidate:
    def test_invalidate_key(self):
        s = _svc()
        with patch.object(s, "_sql", return_value=[]) as m:
            assert s.invalidate("k", "ns") is True
            assert "UPDATE" in m.call_args[0][0].upper()

    def test_invalidate_namespace_counts(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"cnt": 3}], []]):
            assert s.invalidate_namespace("ns") == 3

    def test_invalidate_namespace_empty(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"cnt": 0}]]):
            assert s.invalidate_namespace("ns") == 0


class TestVacuumAndStats:
    def test_vacuum_counts(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"cnt": 5}], []]):
            assert s.vacuum() == 5

    def test_vacuum_nothing(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"cnt": 0}]]):
            assert s.vacuum() == 0

    def test_get_recent_entries(self):
        s = _svc()
        rows = [{"cache_key": "h", "cache_ns": "ns", "value_json": json.dumps({"x": 1})}]
        with patch.object(s, "_sql", return_value=rows):
            out = s.get_recent_entries(limit=5)
        assert out and out[0]["data"] == {"x": 1}

    def test_get_recent_entries_bad_json_skipped(self):
        s = _svc()
        rows = [{"cache_key": "h", "cache_ns": "ns", "value_json": "not json{"}]
        with patch.object(s, "_sql", return_value=rows):
            out = s.get_recent_entries()
        assert out == []

    def test_stats(self):
        s = _svc()
        row = [{"total_entries": 10, "live_entries": 8, "expired_entries": 2,
                "total_hits": 40, "max_hits_single_key": 9, "namespaces": 3}]
        with patch.object(s, "_sql", return_value=row):
            out = s.stats()
        assert out["total_entries"] == 10

    def test_stats_error(self):
        s = _svc()
        with patch.object(s, "_sql", side_effect=RuntimeError("x")):
            assert "error" in s.stats()


class TestEnsureTable:
    def test_ensure_table_runs_create(self):
        s = DeltaCacheService()  # _ready False
        with patch.object(s, "_sql", return_value=[]) as m:
            s._ensure_table()
        assert s._ready is True
        assert "CREATE TABLE" in m.call_args[0][0]

    def test_ensure_table_error_non_fatal(self):
        s = DeltaCacheService()
        with patch.object(s, "_sql", side_effect=RuntimeError("no perms")):
            s._ensure_table()  # must not raise
        assert s._ready is False
