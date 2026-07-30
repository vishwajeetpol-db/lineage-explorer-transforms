"""Unit tests for backend.circuit_breaker.

Covers the CLOSED / OPEN / HALF_OPEN state machine:
- record_failure opens after threshold; check() raises 503 when OPEN
- record_success closes and resets count
- recovery_timeout elapse -> HALF_OPEN on next state read
- reset(); get_status() shape; log-branch coverage
"""
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.circuit_breaker import CircuitBreaker, CircuitState, sql_circuit_breaker


def _cb(**kw):
    return CircuitBreaker(name="test", failure_threshold=3, recovery_timeout=30, **kw)


class TestClosedState:
    def test_starts_closed(self):
        cb = _cb()
        assert cb.state == CircuitState.CLOSED

    def test_check_passes_when_closed(self):
        cb = _cb()
        cb.check()  # no raise

    def test_failures_below_threshold_stay_closed(self):
        cb = _cb()
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CircuitState.CLOSED
        cb.check()


class TestOpenState:
    def test_opens_at_threshold(self):
        cb = _cb()
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN

    def test_check_raises_503_when_open(self):
        cb = _cb()
        for _ in range(3):
            cb.record_failure()
        with pytest.raises(HTTPException) as ei:
            cb.check()
        assert ei.value.status_code == 503
        assert "circuit breaker OPEN" in ei.value.detail

    def test_further_failures_while_open_stay_open(self):
        cb = _cb()
        for _ in range(5):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN


class TestHalfOpenState:
    def test_transitions_to_half_open_after_timeout(self):
        cb = _cb()
        for _ in range(3):
            cb.record_failure()
        # Fast-forward past the recovery window by rewinding last_failure_time.
        cb._last_failure_time = time.time() - 31
        assert cb.state == CircuitState.HALF_OPEN
        # check() does not raise in HALF_OPEN
        cb.check()

    def test_success_in_half_open_closes(self):
        cb = _cb()
        for _ in range(3):
            cb.record_failure()
        cb._last_failure_time = time.time() - 31
        assert cb.state == CircuitState.HALF_OPEN
        cb.record_success()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


class TestRecordSuccess:
    def test_resets_failure_count(self):
        cb = _cb()
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        assert cb._failure_count == 0
        assert cb.state == CircuitState.CLOSED

    def test_success_when_already_closed_is_noop(self):
        cb = _cb()
        cb.record_success()
        assert cb.state == CircuitState.CLOSED


class TestReset:
    def test_manual_reset(self):
        cb = _cb()
        for _ in range(3):
            cb.record_failure()
        assert cb.state == CircuitState.OPEN
        cb.reset()
        assert cb.state == CircuitState.CLOSED
        assert cb._failure_count == 0


class TestGetStatus:
    def test_status_no_failures(self):
        cb = _cb()
        st = cb.get_status()
        assert st["name"] == "test"
        assert st["state"] == "CLOSED"
        assert st["failure_count"] == 0
        assert st["failure_threshold"] == 3
        assert st["recovery_timeout_sec"] == 30
        assert st["last_failure_age_sec"] is None

    def test_status_after_failure(self):
        cb = _cb()
        cb.record_failure()
        st = cb.get_status()
        assert st["failure_count"] == 1
        assert isinstance(st["last_failure_age_sec"], int)


class TestSingleton:
    def test_singleton_defaults(self):
        assert sql_circuit_breaker.name == "sql_warehouse"
        assert sql_circuit_breaker.failure_threshold == 5
        assert sql_circuit_breaker.recovery_timeout == 30
