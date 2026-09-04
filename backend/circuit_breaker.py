"""Circuit breaker for SQL warehouse calls — C8 fix.

Prevents cascading failures when the warehouse is stopped, overloaded, or timing out.
Three states:
  CLOSED  — normal operation, calls pass through
  OPEN    — too many recent failures, calls fail-fast with 503
  HALF_OPEN — after cooldown, allow one probe call

Usage:
    from backend.circuit_breaker import sql_circuit_breaker

    # In any _execute_sql wrapper:
    sql_circuit_breaker.check()  # raises HTTPException(503) if OPEN
    try:
        result = _actual_sql_call(...)
        sql_circuit_breaker.record_success()
        return result
    except Exception as e:
        sql_circuit_breaker.record_failure()
        raise
"""
import time
import threading
import logging
from enum import Enum
from fastapi import HTTPException

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    """Thread-safe circuit breaker for warehouse SQL calls.

    C8 FIX: Prevents 50s timeout cascades when warehouse is stopped.
    Fast-fails with 503 after `failure_threshold` consecutive failures.
    Resets after `recovery_timeout` seconds to probe health.
    """

    def __init__(
        self,
        name: str = "sql_warehouse",
        failure_threshold: int = 5,
        recovery_timeout: int = 30,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery window has elapsed
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    logger.info(f"Circuit breaker [{self.name}]: HALF_OPEN (probing)")
            return self._state

    def check(self) -> None:
        """Check circuit state; raise 503 if OPEN.

        Called before every SQL execution. If the warehouse has been
        consistently failing, this prevents the 50s timeout pile-up (C8).
        """
        current = self.state
        if current == CircuitState.OPEN:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"SQL warehouse temporarily unavailable (circuit breaker OPEN). "
                    f"The warehouse had {self.failure_threshold}+ consecutive failures. "
                    f"Retrying in {self.recovery_timeout}s."
                ),
            )

    def record_success(self) -> None:
        """Record a successful SQL call — close the circuit."""
        with self._lock:
            if self._state != CircuitState.CLOSED:
                logger.info(f"Circuit breaker [{self.name}]: CLOSED (recovered)")
            self._failure_count = 0
            self._state = CircuitState.CLOSED

    def record_failure(self) -> None:
        """Record a failed SQL call — potentially open the circuit."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._failure_count >= self.failure_threshold:
                if self._state != CircuitState.OPEN:
                    logger.warning(
                        f"Circuit breaker [{self.name}]: OPEN after "
                        f"{self._failure_count} failures"
                    )
                self._state = CircuitState.OPEN

    def reset(self) -> None:
        """Manual reset (e.g., admin action or config change)."""
        with self._lock:
            self._failure_count = 0
            self._state = CircuitState.CLOSED
            logger.info(f"Circuit breaker [{self.name}]: manually reset")

    def get_status(self) -> dict:
        """Status for admin dashboard / diagnostics."""
        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self._failure_count,
            "failure_threshold": self.failure_threshold,
            "recovery_timeout_sec": self.recovery_timeout,
            "last_failure_age_sec": (
                int(time.time() - self._last_failure_time)
                if self._last_failure_time > 0 else None
            ),
        }


# Singleton circuit breaker for the SQL warehouse
sql_circuit_breaker = CircuitBreaker(
    name="sql_warehouse",
    failure_threshold=5,
    recovery_timeout=30,
)
