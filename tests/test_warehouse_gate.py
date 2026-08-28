"""B3/E1: admission control, circuit breaking and attribution at one chokepoint.

The app could present ~84 concurrent statements (a 64-thread default executor plus
three module pools) at a warehouse that runs ~10 per cluster, with no semaphore
anywhere on the SQL path. A queued statement still holds the app thread that
submitted it for up to 170s, so warehouse queuing became thread exhaustion and cheap
queries timed out behind expensive ones.
"""
import threading
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import backend.warehouse_gate as wg


@pytest.fixture(autouse=True)
def _clean():
    wg.reset_stats()
    wg.clear_request_context()
    wg.sql_circuit_breaker.reset()
    yield
    wg.reset_stats()
    wg.clear_request_context()
    wg.sql_circuit_breaker.reset()


class TestAdmissionControl:
    def test_slot_is_released_on_success(self):
        for _ in range(wg.MAX_CONCURRENT_STATEMENTS + 3):
            with wg.warehouse_slot():
                pass
        assert wg.get_stats()["in_flight"] == 0

    def test_slot_is_released_on_failure(self):
        """A leaked slot would shrink capacity permanently after any query error."""
        with pytest.raises(RuntimeError):
            with wg.warehouse_slot():
                raise RuntimeError("query failed")
        assert wg.get_stats()["in_flight"] == 0

    def test_concurrency_is_actually_capped(self):
        """The real property: never more than N statements in flight at once."""
        seen_peak = []
        barrier_release = threading.Event()

        def worker():
            with wg.warehouse_slot():
                seen_peak.append(wg.get_stats()["in_flight"])
                barrier_release.wait(timeout=5)

        with patch.object(wg, "MAX_CONCURRENT_STATEMENTS", 3), \
             patch.object(wg, "_slots", threading.BoundedSemaphore(3)), \
             patch.object(wg, "ADMISSION_WAIT_S", 0.05):
            threads = [threading.Thread(target=worker) for _ in range(3)]
            for t in threads:
                t.start()
            while len(seen_peak) < 3 and any(t.is_alive() for t in threads):
                time.sleep(0.01)
            # A 4th caller must be shed, not queued behind the three holders.
            with pytest.raises(wg.WarehouseBusyError):
                with wg.warehouse_slot():
                    pass
            barrier_release.set()
            for t in threads:
                t.join(timeout=5)

        assert max(seen_peak) <= 3

    def test_shed_is_fast_not_a_long_queue(self):
        """Shedding must release the caller's thread quickly — that is the point."""
        with patch.object(wg, "_slots", threading.BoundedSemaphore(1)), \
             patch.object(wg, "ADMISSION_WAIT_S", 0.2):
            with wg.warehouse_slot():
                t0 = time.monotonic()
                with pytest.raises(wg.WarehouseBusyError):
                    with wg.warehouse_slot():
                        pass
                elapsed = time.monotonic() - t0
        assert 0.15 <= elapsed < 2.0

    def test_shed_counted_separately_from_admitted(self):
        with patch.object(wg, "_slots", threading.BoundedSemaphore(1)), \
             patch.object(wg, "ADMISSION_WAIT_S", 0.01):
            with wg.warehouse_slot():
                with pytest.raises(wg.WarehouseBusyError):
                    with wg.warehouse_slot():
                        pass
        st = wg.get_stats()
        assert st["shed"] == 1 and st["admitted"] == 1
        assert 0 < st["shed_rate_pct"] < 100


class TestCircuitBreakerIntegration:
    def test_failures_open_the_breaker(self):
        """The breaker previously sat in two routers and never saw the lineage path."""
        for _ in range(wg.sql_circuit_breaker.failure_threshold):
            with pytest.raises(RuntimeError):
                with wg.warehouse_slot():
                    raise RuntimeError("warehouse down")
        with pytest.raises(HTTPException) as exc:
            with wg.warehouse_slot():
                pass
        assert exc.value.status_code == 503

    def test_our_own_shed_does_not_open_the_breaker(self):
        """Backpressure is not a warehouse failure.

        Counting a shed as a failure would let a busy minute trip the breaker and turn
        our own throttling into a hard outage for everyone.
        """
        with patch.object(wg, "_slots", threading.BoundedSemaphore(1)), \
             patch.object(wg, "ADMISSION_WAIT_S", 0.01):
            with wg.warehouse_slot():
                for _ in range(wg.sql_circuit_breaker.failure_threshold + 2):
                    with pytest.raises(wg.WarehouseBusyError):
                        with wg.warehouse_slot():
                            pass
        assert wg.sql_circuit_breaker.state.value == "CLOSED"


class TestAttribution:
    def test_untagged_when_outside_a_request(self):
        """Background work is not attributable to a person; it must not claim to be."""
        wg.clear_request_context()
        assert wg.tag_statement("SELECT 1") == "SELECT 1"

    def test_anonymous_request_is_still_tagged(self):
        """No email header is not the same as no request — keep the endpoint signal."""
        wg.set_request_context("/api/lineage", "")
        out = wg.tag_statement("SELECT 1")
        assert "endpoint=/api/lineage" in out and "actor=anon" in out

    def test_tag_carries_endpoint_and_hashed_actor(self):
        wg.set_request_context("/api/lineage/{catalog}", "alice@example.com")
        out = wg.tag_statement("SELECT 1")
        assert out.startswith("/*") and out.endswith("SELECT 1")
        assert "endpoint=/api/lineage/{catalog}" in out
        assert "app=bricktrace" in out

    def test_email_is_never_in_the_tag(self):
        """The tag lands in system.query.history; it must correlate, not identify."""
        wg.set_request_context("/api/x", "alice@example.com")
        out = wg.tag_statement("SELECT 1")
        assert "alice@example.com" not in out
        assert "alice" not in out
        assert wg.hash_actor("alice@example.com") in out

    def test_same_user_hashes_stably(self):
        assert wg.hash_actor("a@b.com") == wg.hash_actor("a@b.com")
        assert wg.hash_actor("a@b.com") != wg.hash_actor("c@d.com")

    def test_comment_terminator_cannot_escape_the_comment(self):
        """A `*/` in the tag would close it early and splice the rest into code."""
        wg.set_request_context("/api/x*/ ; DROP TABLE t; --", "u@e.com")
        out = wg.tag_statement("SELECT 1")
        header = out.split("\n", 1)[0]
        assert header.count("*/") == 1, f"comment escaped: {header}"
        assert "DROP TABLE" not in header or header.index("*/") > header.index("DROP TABLE")

    def test_newlines_are_stripped_from_the_tag(self):
        wg.set_request_context("/api/x\nSELECT 2", "u@e.com")
        assert len(wg.tag_statement("SELECT 1").split("\n")) == 2

    def test_context_propagates_into_a_worker_thread(self):
        """asyncio.to_thread copies the context; a thread-local would be empty here."""
        import contextvars
        wg.set_request_context("/api/deep", "bob@example.com")
        result = {}

        def work():
            result["tag"] = wg.tag_statement("SELECT 1")

        ctx = contextvars.copy_context()
        t = threading.Thread(target=lambda: ctx.run(work))
        t.start()
        t.join(timeout=5)
        assert "endpoint=/api/deep" in result["tag"]

    def test_current_actor_matches_the_tag(self):
        wg.set_request_context("/api/x", "carol@example.com")
        assert wg.current_actor() == wg.hash_actor("carol@example.com")
