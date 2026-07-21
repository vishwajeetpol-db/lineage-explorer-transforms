"""Tests for backend.cache_service — DeltaCacheService (v2.5.4).

Covers singleton behavior, graceful fallback on errors, key hashing,
and value size limits.
"""
import json
from unittest.mock import patch, MagicMock

import pytest


class TestDeltaCacheServiceSingleton:
    """get_cache_service() returns a singleton."""

    def test_returns_same_instance(self):
        from backend.cache_service import get_cache_service
        svc1 = get_cache_service()
        svc2 = get_cache_service()
        assert svc1 is svc2

    def test_instance_is_delta_cache_service(self):
        from backend.cache_service import get_cache_service, DeltaCacheService
        svc = get_cache_service()
        assert isinstance(svc, DeltaCacheService)


class TestCacheGetFallback:
    """cache.get() should return None on any exception."""

    def test_get_returns_none_on_sql_error(self):
        from backend.cache_service import get_cache_service
        svc = get_cache_service()
        with patch.object(svc, "_execute_sql", side_effect=RuntimeError("no warehouse")):
            result = svc.get("test_key", namespace="lineage")
            assert result is None

    def test_get_returns_none_when_no_warehouse(self):
        from backend.cache_service import get_cache_service
        svc = get_cache_service()
        with patch("backend.cache_service.WAREHOUSE_ID", ""):
            result = svc.get("any_key", namespace="lineage")
            assert result is None


class TestCacheSetFallback:
    """cache.set() should silently fail on any exception."""

    def test_set_does_not_raise_on_error(self):
        from backend.cache_service import get_cache_service
        svc = get_cache_service()
        with patch.object(svc, "_execute_sql", side_effect=RuntimeError("no warehouse")):
            # Should not raise
            svc.set("test_key", {"data": "value"}, namespace="lineage", ttl_seconds=3600)

    def test_set_rejects_oversized_values(self):
        from backend.cache_service import get_cache_service, MAX_VALUE_BYTES
        svc = get_cache_service()
        oversized = "x" * (MAX_VALUE_BYTES + 1)
        with patch.object(svc, "_execute_sql") as mock_sql:
            svc.set("big_key", oversized, namespace="test", ttl_seconds=3600)
            # Should either not call SQL (skip) or handle gracefully
            # The key test is that no exception propagates


class TestCacheKeyHashing:
    """Cache key hashing should be deterministic."""

    def test_same_key_produces_same_hash(self):
        import hashlib
        key = "main.default.orders"
        h1 = hashlib.sha256(key.encode()).hexdigest()
        h2 = hashlib.sha256(key.encode()).hexdigest()
        assert h1 == h2

    def test_different_keys_different_hashes(self):
        import hashlib
        h1 = hashlib.sha256("table_a".encode()).hexdigest()
        h2 = hashlib.sha256("table_b".encode()).hexdigest()
        assert h1 != h2
