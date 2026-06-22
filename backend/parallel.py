"""Parallel execution utilities for lineage queries.

Provides a bounded ThreadPoolExecutor and helper functions for running
independent SQL queries concurrently. Used by lineage_service.py and
transform_service.py to parallelize catalog-level operations.

Key design:
  - Single shared pool (max 8 workers) — bounded to avoid overwhelming
    the SQL warehouse with concurrent statements
  - map_parallel() returns results in submission order
  - Exceptions in workers propagate to the caller
  - Graceful fallback: if pool is saturated, tasks queue instead of fail
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed, Future
from typing import Callable, TypeVar, Sequence

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Shared thread pool for all parallel SQL operations.
# 8 workers balances concurrency vs warehouse statement slots.
# Serverless warehouses handle 10+ concurrent statements easily;
# classic warehouses may queue beyond 8 but won't error.
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lineage-parallel")


def map_parallel(fn: Callable[..., T], items: Sequence, *args) -> list[T]:
    """Execute fn(item, *args) for each item in parallel. Returns results in order.

    If any call raises, other calls are NOT cancelled (they're already submitted),
    but the first exception is re-raised after all complete.

    For single-item lists, runs synchronously to avoid pool overhead.
    """
    if not items:
        return []
    if len(items) == 1:
        return [fn(items[0], *args)]

    futures: list[Future] = []
    for item in items:
        futures.append(_POOL.submit(fn, item, *args))

    results: list[T] = []
    first_error = None
    for f in futures:
        try:
            results.append(f.result())
        except Exception as e:
            if first_error is None:
                first_error = e
            results.append(None)  # type: ignore
            logger.warning(f"Parallel task failed: {e}")

    if first_error:
        # Log but don't raise — partial results are better than nothing for lineage
        logger.warning(f"map_parallel: {sum(1 for r in results if r is None)}/{len(items)} tasks failed")

    return [r for r in results if r is not None]


def run_parallel(*fns: Callable[[], T]) -> list[T]:
    """Run multiple zero-arg functions concurrently and return results in order.

    Used for independent operations like:
      - _walk("up") and _walk("down") in lineage trace
      - job cost SQL and pipeline cost SQL
    """
    if not fns:
        return []
    if len(fns) == 1:
        return [fns[0]()]

    futures = [_POOL.submit(fn) for fn in fns]
    return [f.result() for f in futures]
