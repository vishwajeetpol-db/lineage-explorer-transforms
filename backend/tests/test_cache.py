"""
Cache primitives — correctness under concurrent load.

These tests exist because of the 2026-04-23 incident: the old hand-rolled
cache had a release bug under concurrent requests. The refactor to
cachetools + per-key locks is covered here with a race test that would
have caught the original bug.
"""
import threading
import time

from backend import lineage_service
from backend.models import LineageResponse


def test_set_and_get():
    lineage_service._cache_set("k1", {"hello": "world"})
    assert lineage_service._cache_get("k1") == {"hello": "world"}


def test_get_missing_returns_none():
    assert lineage_service._cache_get("nope") is None


def test_invalidate_all():
    lineage_service._cache_set("a", 1)
    lineage_service._cache_set("b", 2)
    lineage_service.invalidate_cache()
    assert lineage_service._cache_get("a") is None
    assert lineage_service._cache_get("b") is None


def test_invalidate_prefix():
    lineage_service._cache_set("lineage:foo", 1)
    lineage_service._cache_set("lineage:bar", 2)
    lineage_service._cache_set("columns:baz", 3)
    lineage_service.invalidate_cache(prefix="lineage:")
    assert lineage_service._cache_get("lineage:foo") is None
    assert lineage_service._cache_get("lineage:bar") is None
    assert lineage_service._cache_get("columns:baz") == 3


def test_evict_specific_entry():
    lineage_service._cache_set("k", "v")
    assert lineage_service.evict_cache_entry("k") is True
    assert lineage_service.evict_cache_entry("k") is False   # second call — not found
    assert lineage_service._cache_get("k") is None


def test_snapshot_reports_entries():
    lineage_service._cache_set("snap1", {"x": 1})
    lineage_service._cache_set("snap2", [1, 2, 3])
    entries, total_bytes, inflight = lineage_service.get_cache_snapshot()
    keys = {k for k, *_ in entries}
    assert "snap1" in keys and "snap2" in keys
    assert total_bytes > 0
    assert inflight == []   # nothing in flight when no fetches are active


def test_single_flight_coalesces_concurrent_fetchers():
    """THE regression test for the 2026-04-23 cache release bug.

    When 20 threads race to fetch the same key, the fetcher must run exactly
    once. The hand-rolled cache had release paths that could let multiple
    threads slip through — that's what we're guarding against."""
    call_count = {"n": 0}
    barrier = threading.Barrier(20)

    def slow_fetch():
        call_count["n"] += 1
        time.sleep(0.1)   # simulate DBSQL roundtrip
        return ["result"]

    def worker():
        barrier.wait()   # line up all 20 threads at the starting line
        result = lineage_service._cached_fetch("race-key", slow_fetch)
        assert result == ["result"]

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count["n"] == 1, (
        f"single-flight broken: fetcher called {call_count['n']} times under 20-thread race"
    )


def test_cached_fetch_serves_cache_on_subsequent_calls():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return [1, 2, 3]

    r1 = lineage_service._cached_fetch("key", fetch)
    r2 = lineage_service._cached_fetch("key", fetch)
    r3 = lineage_service._cached_fetch("key", fetch)
    assert r1 == r2 == r3 == [1, 2, 3]
    assert calls["n"] == 1


def test_cached_fetch_skip_cache_bypasses():
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        return {"v": calls["n"]}

    r1 = lineage_service._cached_fetch("key", fetch)
    r2 = lineage_service._cached_fetch("key", fetch, skip_cache=True)
    assert r1 == {"v": 1}
    assert r2 == {"v": 2}
    assert calls["n"] == 2


def test_wrap_with_cache_metadata_does_not_mutate_cached_value():
    """THE other regression test: the old code mutated the cached Pydantic
    response in place, so concurrent readers could observe partial updates.
    The refactor uses model_copy; the cached reference must stay pristine."""
    response = LineageResponse(nodes=[], edges=[])
    lineage_service._cache_set("lineage:foo.bar", response)

    cached_before = lineage_service._cache_get("lineage:foo.bar")
    assert cached_before.cached is False
    assert cached_before.cached_at is None

    wrapped = lineage_service._wrap_with_cache_metadata(
        cached_before, "lineage:foo.bar", from_cache=True, fetch_ms=0
    )

    # The copy has the metadata
    assert wrapped.cached is True
    assert wrapped.cached_at is not None
    # The cached original DOES NOT — no in-place mutation
    assert cached_before.cached is False, "cache-backed object was mutated"
    assert wrapped is not cached_before
