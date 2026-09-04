"""Startup hook — call activate() once after all backend modules are loaded.

Usage in main.py's lifespan or imports:
    from backend.startup import activate
    activate()

This triggers the parallelism patches that replace sequential catalog
enumeration, BFS trace walks, and cost cache refresh with concurrent versions.
Also runs edge-case guards (C1, C7, C14) for runtime health checks.
"""
import logging

logger = logging.getLogger(__name__)
_activated = False


def activate():
    """Activate performance patches and edge-case guards. Idempotent."""
    global _activated
    if _activated:
        return
    _activated = True
    try:
        import backend.perf_patches  # noqa: F401
        logger.info("Performance patches activated successfully")
    except Exception as e:
        logger.warning(f"Performance patches failed to load (non-fatal, app continues): {e}")

    # Edge-case guards: C1 (system tables health), C7 (feature flags),
    # C14 (soft-warm cache). All non-fatal.
    try:
        from backend.edge_case_guards import run_startup_checks
        from backend.lineage_service import _execute_sql
        results = run_startup_checks(_execute_sql)
        logger.info(f"Edge-case startup checks completed: {list(results.keys())}")
    except Exception as e:
        logger.warning(f"Edge-case startup checks failed (non-fatal): {e}")
