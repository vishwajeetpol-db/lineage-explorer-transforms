"""Full-path coverage for backend.cache_service.DeltaCacheService.

The existing test_cache_service.py covers fallbacks; this drives the SQL-success
paths (get hit, set MERGE, invalidate, vacuum, get_recent_entries, stats) by
mocking the DeltaCacheService._sql method to return crafted rows.
"""
import json
import os
from unittest.mock import patch

import pytest

import backend.cache_service as cs
from backend.cache_service import get_cache_service, DeltaCacheService, MAX_VALUE_BYTES


@pytest.fixture(autouse=True)
def _no_hit_buffer_leak():
    """hit_count buffering is module-global; keep it out of sibling tests."""
    cs._reset_hit_buffer()
    yield
    cs._reset_hit_buffer()


def _incompressible(nbytes: int) -> str:
    """A payload gzip cannot shrink — os.urandom via latin-1 is already max entropy."""
    return os.urandom(nbytes).decode("latin-1")


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
        """Still rejected when it genuinely does not fit — measured AFTER compression.

        The payload has to be incompressible for this to mean anything: a run of
        identical bytes gzips to almost nothing, so the old `"x" * (cap + 1)` fixture
        would now be stored (correctly) and the test would be asserting the opposite
        of the behaviour it was written for.
        """
        s = _svc()
        with patch.object(s, "_sql") as m:
            assert s.set("k", _incompressible(MAX_VALUE_BYTES + 5_000), namespace="ns") is False
            m.assert_not_called()

    def test_oversized_but_compressible_is_now_cached(self):
        """The point of compression: big repetitive graphs stop being uncacheable.

        A graph near LINEAGE_MAX_NODES is highly repetitive JSON and used to blow the
        256KB cap, so the single most expensive query in the app was the one that
        never cached — silently, at debug level.
        """
        s = _svc()
        graph = {"nodes": [{"full_name": f"cat.sch.table_{i}", "table_type": "MANAGED",
                            "owner": "someone@example.com", "columns": []} for i in range(4000)]}
        assert len(json.dumps(graph).encode()) > MAX_VALUE_BYTES
        with patch.object(s, "_sql", return_value=[]) as m:
            assert s.set("k", graph, namespace="ns") is True
            assert "MERGE INTO" in m.call_args[0][0]

    def test_compressed_value_round_trips(self):
        s = _svc()
        payload = {"nodes": [{"id": f"n{i}", "name": f"table_{i}"} for i in range(2000)]}
        stored = DeltaCacheService._encode(json.dumps(payload))
        assert stored.startswith(cs._GZIP_MARKER)
        with patch.object(s, "_sql", side_effect=[[{"value_json": stored}], []]):
            assert s.get("k", "ns") == payload

    def test_uncompressed_rows_still_readable(self):
        """Rows written before compression existed are plain JSON — reads must sniff."""
        s = _svc()
        with patch.object(s, "_sql", side_effect=[[{"value_json": json.dumps({"legacy": True})}], []]):
            assert s.get("k", "ns") == {"legacy": True}

    def test_small_value_is_not_compressed(self):
        """gzip+base64 grows tiny payloads; the shorter form has to win."""
        assert not DeltaCacheService._encode('{"a": 1}').startswith(cs._GZIP_MARKER)


class TestHitCountBuffering:
    """A cache HIT must not issue a Delta write.

    Delta takes table-level optimistic concurrency, so a write on the read path made
    the cache degrade as load rose — concurrent readers serializing on commit or
    retrying a conflict, which is the opposite of what a cache is for.
    """

    def test_hit_issues_no_write(self):
        s = _svc()
        with patch.object(s, "_sql", return_value=[{"value_json": json.dumps({"a": 1})}]) as m:
            assert s.get("k", "ns") == {"a": 1}
        assert m.call_count == 1, "a cache hit must be exactly one SELECT"
        assert "UPDATE" not in m.call_args_list[0][0][0].upper()

    def test_hits_accumulate_then_flush_once(self):
        s = _svc()
        row = [{"value_json": json.dumps({"a": 1})}]
        with patch.object(cs, "_HIT_FLUSH_INTERVAL_S", 0.0), \
             patch.object(s, "_sql", return_value=row) as m:
            for _ in range(5):
                s.get("k", "ns")
            statements = [c[0][0] for c in m.call_args_list]
        updates = [q for q in statements if "UPDATE" in q.upper()]
        assert len(updates) <= 5 and updates, "hits should flush, batched"
        assert "hit_count" in updates[0] and "CASE" in updates[0]

    def test_flush_failure_does_not_lose_the_value(self):
        """Telemetry is best-effort; a failed flush must never break a cache read."""
        s = _svc()
        calls = {"n": 0}

        def _sql(stmt):
            calls["n"] += 1
            if "UPDATE" in stmt.upper():
                raise RuntimeError("concurrent modification")
            return [{"value_json": json.dumps({"a": 1})}]

        with patch.object(cs, "_HIT_FLUSH_INTERVAL_S", 0.0), \
             patch.object(s, "_sql", side_effect=_sql):
            assert s.get("k", "ns") == {"a": 1}
        assert calls["n"] == 2

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
