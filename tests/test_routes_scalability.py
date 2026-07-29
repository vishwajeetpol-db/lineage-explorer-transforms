"""Tests for backend/routes/scalability.py — pagination + distributed cache (cap 18).

Cursor-paginated graph, cache stats, admin-gated namespace invalidation, health.
"""
from unittest.mock import patch

import pytest


class TestPaginatedGraph:
    def test_requires_catalog(self, app_client):
        resp = app_client.get("/api/scalability/graph")
        assert resp.status_code == 422

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/scalability/graph", params={"catalog": "bad;"})
        assert resp.status_code == 400

    def test_page_size_bounds_422(self, app_client):
        resp = app_client.get("/api/scalability/graph", params={
            "catalog": "main", "page_size": 5000})
        assert resp.status_code == 422


class TestCacheStats:
    def test_cache_stats_ok(self, app_client):
        with patch("backend.cache_service.DeltaCacheService.stats", return_value={"total_entries": 0}):
            resp = app_client.get("/api/scalability/cache/stats")
        assert resp.status_code == 200


class TestCacheInvalidate:
    def test_invalidate_requires_admin(self, non_admin_client):
        resp = non_admin_client.post("/api/scalability/cache/invalidate",
                                     json={"namespace": "lineage"})
        assert resp.status_code == 403

    def test_invalidate_admin_ok(self, admin_client):
        with patch("backend.cache_service.DeltaCacheService.invalidate_namespace", return_value=3):
            resp = admin_client.post("/api/scalability/cache/invalidate",
                                     json={"namespace": "lineage"})
        assert resp.status_code == 200


class TestScalabilityHealth:
    def test_health_requires_catalog(self, app_client):
        resp = app_client.get("/api/scalability/health")
        assert resp.status_code == 422
