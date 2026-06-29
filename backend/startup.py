"""Startup hook — call activate() once after all backend modules are loaded.

Usage in main.py's lifespan or imports:
    from backend.startup import activate
    activate()

This triggers the parallelism patches that replace sequential catalog
enumeration, BFS trace walks, and cost cache refresh with concurrent versions.
"""
import logging

logger = logging.getLogger(__name__)
_activated = False


def activate():
    """Activate performance patches. Idempotent — safe to call multiple times."""
    global _activated
    if _activated:
        return
    _activated = True
    try:
        import backend.perf_patches  # noqa: F401
        logger.info("Performance patches activated successfully")
    except Exception as e:
        logger.warning(f"Performance patches failed to load (non-fatal, app continues): {e}")
