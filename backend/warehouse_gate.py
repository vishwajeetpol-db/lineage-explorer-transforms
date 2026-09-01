"""Admission control and attribution for every SQL warehouse statement.

WHY THIS EXISTS
  The app can present far more concurrent statements than a warehouse will run.
  Thread capacity was the only limit: 64 workers on the asyncio default executor
  (main.py) plus three module pools (8 + 8 + 4), fed from ~145 `asyncio.to_thread`
  call sites. A SQL warehouse admits roughly ten concurrent queries per cluster
  before queuing, scaling out only to its configured max cluster count.

  Worse, a QUEUED statement still occupies the app thread that submitted it for up
  to SQL_WAIT_TIMEOUT + SQL_POLL_MAX_S (50s + 120s). So without a gate the app
  converts warehouse queuing into thread exhaustion, and users whose queries would
  have been cheap time out behind a handful of expensive ones. Nothing about any
  single request looks abusive while this happens.

  This module is the one chokepoint every statement passes through. It does three
  things there, because all three want the same location:

    1. ADMISSION CONTROL — a bounded semaphore sized to what the warehouse can
       actually run, not to how many threads happen to exist. When it is full,
       callers wait briefly and then are REJECTED with a 429 and a Retry-After.
       Queuing visibly in the app is honest; queuing invisibly in the warehouse
       while holding a thread is not.

    2. CIRCUIT BREAKING — the breaker already existed but was wired into two
       routers (dq, capability_closures) and NOT the lineage path, which is where
       every heavy query in the app lives. Moving it here means the component that
       sheds load finally sees the traffic that would trip it.

    3. ATTRIBUTION — every statement is prefixed with a SQL comment naming the
       endpoint and a hashed user id. Databricks retains statement text in
       system.query.history, so this makes the platform's own audit trail answer
       "which user and which endpoint caused this warehouse spike" without the app
       needing to export anything. The hash is truncated and unsalted-by-design:
       it correlates a user's own queries with each other, and is not intended to
       resist reversal by someone who already has the workspace's user list.

SIZING
  WAREHOUSE_MAX_CONCURRENT_STATEMENTS should track the warehouse's real
  concurrency (cluster size x max clusters), NOT the app's thread count. Too high
  and the gate does nothing; too low and the app throttles a warehouse that had
  headroom. The default of 8 is deliberately conservative for a single-cluster
  Small warehouse — raise it after checking queue depth in system.query.history.

KNOWN LIMIT: THIS BOUNDS TOTAL CONCURRENCY, NOT PER-USER FAIRNESS
  The semaphore is global, so one expensive request can legitimately hold every
  slot: a catalog-wide trace fans out across `parallel._POOL` (8 workers) and a
  catalog enumeration does the same, either of which can occupy all 8 default slots
  and make concurrent users wait ADMISSION_WAIT_S and then get a 429.

  That is a real trade and it is the right one to make first — an app that shed a
  few requests is recoverable, an app that has exhausted its threads against a
  queued warehouse is not, and raising the ceiling past the warehouse's real
  capacity just moves the queue back where it cannot be seen. But it does mean the
  429s under load will not be evenly distributed.

  Fixing fairness needs a different mechanism than a ceiling: per-user slot
  reservation (a small guaranteed allocation each, with the remainder shared), or
  weighting by request cost so a catalog-wide scan cannot claim the same share as a
  single-table lookup. Worth doing once `shed_rate_pct` in get_stats() shows this
  actually biting real users, and not before — the counters are there to answer
  that question with data rather than guesswork.
"""
import hashlib
import logging
import os
import threading
import time
from contextvars import ContextVar

from backend.circuit_breaker import sql_circuit_breaker

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Admission control
# --------------------------------------------------------------------------
MAX_CONCURRENT_STATEMENTS = int(os.environ.get("WAREHOUSE_MAX_CONCURRENT_STATEMENTS", "8"))

# How long a caller waits for a slot before being shed. Deliberately short: the
# point is to fail fast enough that the caller's thread is released, not to move
# the queue from the warehouse into the semaphore.
ADMISSION_WAIT_S = float(os.environ.get("WAREHOUSE_ADMISSION_WAIT_SECONDS", "5"))

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_STATEMENTS)

# Observability counters. Plain ints under the GIL; exact enough for a gauge.
_stats_lock = threading.Lock()
_stats = {
    "admitted": 0,
    "shed": 0,
    "in_flight": 0,
    "peak_in_flight": 0,
    "total_wait_s": 0.0,
}


class WarehouseBusyError(RuntimeError):
    """No warehouse slot became available — the caller should be shed, not queued.

    Distinct from a generic RuntimeError so the API layer can answer 429 with a
    Retry-After instead of a 500. A 429 tells the client to come back; a 500 makes
    a capacity problem look like a defect.
    """


def get_stats() -> dict:
    """Admission-control counters, for /api/diagnostics and the admin dashboard."""
    with _stats_lock:
        s = dict(_stats)
    admitted = s["admitted"]
    s["max_concurrent"] = MAX_CONCURRENT_STATEMENTS
    s["admission_wait_s"] = ADMISSION_WAIT_S
    s["avg_wait_ms"] = round((s.pop("total_wait_s") / admitted) * 1000, 1) if admitted else 0.0
    s["shed_rate_pct"] = (
        round(100.0 * s["shed"] / (admitted + s["shed"]), 2) if (admitted + s["shed"]) else 0.0
    )
    return s


def reset_stats() -> None:
    """Zero the counters — for tests and for an admin-initiated reset."""
    with _stats_lock:
        _stats.update(
            admitted=0, shed=0, in_flight=0, peak_in_flight=0, total_wait_s=0.0
        )


# --------------------------------------------------------------------------
# Attribution context
#
# ContextVar, not thread-local: FastAPI handlers are coroutines, and
# `asyncio.to_thread` copies the current context into the worker thread — so a
# value set once in middleware reaches the SQL call however many threads deep it
# runs. A thread-local would be empty in exactly the threads that issue the SQL.
# --------------------------------------------------------------------------
_endpoint_ctx: ContextVar[str] = ContextVar("bt_endpoint", default="")
_actor_ctx: ContextVar[str] = ContextVar("bt_actor", default="")

_TAG_MAX_LEN = 120


def set_request_context(endpoint: str, actor: str = "") -> None:
    """Record who/what is driving the statements issued for this request."""
    _endpoint_ctx.set(_scrub_tag(endpoint))
    _actor_ctx.set(hash_actor(actor))


def clear_request_context() -> None:
    """Unset the attribution context — statements go out untagged.

    Distinct from `set_request_context("", "")`: for a real request with no email
    header, "anonymous" is a meaningful attribution value (hash_actor returns "anon",
    and the endpoint is still known), so the setter deliberately keeps tagging. This
    is for leaving request scope entirely — background tasks, and test teardown.
    """
    _endpoint_ctx.set("")
    _actor_ctx.set("")


def current_actor() -> str:
    """The hashed actor for the in-flight request, or "" outside one.

    Exposed so other per-user budgets (LLM calls, for instance) can key on the same
    identity the warehouse attribution uses, without re-plumbing an identity through
    every call site.
    """
    return _actor_ctx.get()


def hash_actor(actor: str) -> str:
    """Short stable digest of a user identity, for correlation without the email."""
    if not actor:
        return "anon"
    return hashlib.sha256(actor.encode("utf-8", "replace")).hexdigest()[:12]


def _scrub_tag(value: str) -> str:
    """Make a value safe to embed in a SQL comment.

    A `*/` inside the tag would close the comment early and splice whatever
    follows into executable position, so the sequence is removed rather than
    escaped (SQL block comments have no escape). Newlines go too, since a `--`
    style reader or a log line would break on them.
    """
    if not value:
        return ""
    s = str(value)[:_TAG_MAX_LEN]
    for bad in ("*/", "/*", "\n", "\r", "\t"):
        s = s.replace(bad, " ")
    return "".join(c for c in s if c.isprintable()).strip()


def tag_statement(sql: str) -> str:
    """Prefix a statement with its attribution comment.

    Leading comment, not trailing: Databricks truncates long statement text in
    system.query.history, and the front is the part that survives.
    """
    endpoint = _endpoint_ctx.get()
    actor = _actor_ctx.get()
    if not endpoint and not actor:
        return sql
    parts = ["app=bricktrace"]
    if endpoint:
        parts.append(f"endpoint={endpoint}")
    if actor:
        parts.append(f"actor={actor}")
    return f"/* {' '.join(parts)} */\n{sql}"


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------
class _Slot:
    """Context manager holding one warehouse slot, or raising WarehouseBusyError.

    Also drives the circuit breaker, so `check` / `record_success` /
    `record_failure` happen once per statement in one place instead of being
    re-implemented per router.
    """

    __slots__ = ("_held", "_t0")

    def __enter__(self):
        # Fail fast when the warehouse is known-bad: no point taking a slot to
        # discover it again. Raises HTTPException(503) when OPEN.
        sql_circuit_breaker.check()

        self._t0 = time.monotonic()
        self._held = _slots.acquire(timeout=ADMISSION_WAIT_S)
        waited = time.monotonic() - self._t0
        if not self._held:
            with _stats_lock:
                _stats["shed"] += 1
            logger.warning(
                "Warehouse admission shed after %.1fs wait (%d slots, all busy). "
                "Raise WAREHOUSE_MAX_CONCURRENT_STATEMENTS only if the warehouse "
                "has headroom — check queue depth in system.query.history first.",
                waited, MAX_CONCURRENT_STATEMENTS,
            )
            raise WarehouseBusyError(
                "The SQL warehouse is at capacity right now, so this query was not "
                "submitted. Retry in a few seconds. If this is persistent the "
                "warehouse needs a larger size or more clusters."
            )
        with _stats_lock:
            _stats["admitted"] += 1
            _stats["total_wait_s"] += waited
            _stats["in_flight"] += 1
            if _stats["in_flight"] > _stats["peak_in_flight"]:
                _stats["peak_in_flight"] = _stats["in_flight"]
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._held:
            with _stats_lock:
                _stats["in_flight"] -= 1
            _slots.release()
        # Record the outcome for the breaker. WarehouseBusyError is OUR shed, not a
        # warehouse failure — counting it would open the breaker on our own
        # backpressure and turn a busy minute into a hard outage.
        if exc_type is None:
            sql_circuit_breaker.record_success()
        elif not issubclass(exc_type, WarehouseBusyError):
            sql_circuit_breaker.record_failure()
        return False


def warehouse_slot() -> _Slot:
    """Acquire one warehouse slot for the duration of a `with` block.

        with warehouse_slot():
            resp = client.statement_execution.execute_statement(...)

    Raises WarehouseBusyError if no slot frees up within ADMISSION_WAIT_S, and
    HTTPException(503) if the circuit breaker is open.
    """
    return _Slot()
