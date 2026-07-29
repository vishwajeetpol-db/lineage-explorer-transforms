"""Tests for the capability-cache integration on the 4 panel routes and the
admin eviction endpoints (v2.6.0).

- Impact / Root Cause / Governance / Access accept `refresh` and attach a
  `_cache` meta block; `refresh=true` bypasses the cache.
- GET /api/admin/capability-cache + POST .../evict are admin-gated and validate
  the scope.
"""
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Cached panel routes — _cache meta + refresh bypass
# ---------------------------------------------------------------------------

class TestImpactCaching:
    def test_cache_hit_served_without_recompute(self, app_client):
        """When the cache has the entry, the route returns it and does NOT scan."""
        hit = {"data": {"table_full_name": "main.default.orders", "downstream_count": 4,
                        "downstream_tables": [], "consumer_owners": []},
               "cached_at": "2026-07-20T00:00:00Z", "cached_by": "a@b.com", "stale": False}
        with patch("backend.server.capability_cache.CapabilityCache.get", return_value=hit), \
             patch("backend.routes.impact._compute_impact") as mock_compute:
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["_cache"]["from_cache"] is True
        mock_compute.assert_not_called()

    def test_refresh_bypasses_cache_and_recomputes(self, app_client):
        with patch("backend.server.capability_cache.CapabilityCache.get") as mock_get, \
             patch("backend.server.capability_cache.CapabilityCache.set", return_value=True), \
             patch("backend.routes.impact._compute_impact",
                   return_value={"table_full_name": "main.default.orders",
                                 "downstream_count": 0, "downstream_tables": [],
                                 "consumer_owners": []}) as mock_compute:
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders", "refresh": "true"})
        assert resp.status_code == 200
        assert resp.json()["_cache"]["from_cache"] is False
        mock_get.assert_not_called()
        mock_compute.assert_called_once()

    def test_miss_computes_then_caches(self, app_client):
        with patch("backend.server.capability_cache.CapabilityCache.get", return_value=None), \
             patch("backend.server.capability_cache.CapabilityCache.set", return_value=True) as mock_set, \
             patch("backend.routes.impact._compute_impact",
                   return_value={"table_full_name": "main.default.orders",
                                 "downstream_count": 2, "downstream_tables": [],
                                 "consumer_owners": []}):
            resp = app_client.get("/api/impact", params={
                "catalog": "main", "schema": "default", "table": "orders"})
        assert resp.status_code == 200
        assert resp.json()["_cache"]["from_cache"] is False
        mock_set.assert_called_once()


class TestAccessCaching:
    def test_access_attaches_cache_meta(self, app_client):
        hit = {"data": {"table_full_name": "main.default.orders", "identities": {},
                        "declared_grants": [], "audit_access": [], "recent_events": [],
                        "dormant_grants": [], "unique_empirical_users": 0, "grantee_count": 0,
                        "read_count": 0, "write_count": 0, "lookback_days": 14},
               "cached_at": "t", "cached_by": "a@b.com", "stale": True}
        with patch("backend.server.capability_cache.CapabilityCache.get", return_value=hit):
            resp = app_client.get("/api/access", params={
                "catalog": "main", "schema": "default", "table": "orders"})
        assert resp.status_code == 200
        assert resp.json()["_cache"]["stale"] is True

    def test_access_invalid_param_still_400(self, app_client):
        resp = app_client.get("/api/access", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400


class TestRootCauseCaching:
    def test_refresh_param_accepted(self, app_client):
        with patch("backend.server.capability_cache.CapabilityCache.get") as mock_get, \
             patch("backend.server.capability_cache.CapabilityCache.set", return_value=True), \
             patch("backend.routes.root_cause.trace_root_cause_table",
                   return_value={"focus_table": "main.default.orders", "counts": {},
                                 "prime_suspect": None, "failure_path": [], "flagged": []}):
            resp = app_client.get("/api/root-cause/trace", params={
                "catalog": "main", "schema": "default", "table": "orders", "refresh": "true"})
        assert resp.status_code == 200
        mock_get.assert_not_called()


# ---------------------------------------------------------------------------
# Admin capability-cache endpoints (A2 admin-gate)
# ---------------------------------------------------------------------------

class TestCapabilityCacheAdmin:
    def test_inventory_requires_admin(self, non_admin_client):
        resp = non_admin_client.get("/api/admin/capability-cache")
        assert resp.status_code == 403

    def test_inventory_admin_ok(self, admin_client):
        with patch("backend.server.capability_cache.CapabilityCache.inventory",
                   return_value=[{"table_fqn": "c.s.t", "tab": "impact",
                                  "cached_at": "t", "cached_by": "a@b.com", "stale": False}]):
            resp = admin_client.get("/api/admin/capability-cache")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["entries"][0]["tab"] == "impact"

    def test_evict_requires_admin(self, non_admin_client):
        resp = non_admin_client.post("/api/admin/capability-cache/evict", params={"scope": "all"})
        assert resp.status_code == 403

    def test_evict_all_admin_ok(self, admin_client):
        with patch("backend.server.capability_cache.CapabilityCache.evict_all", return_value=5):
            resp = admin_client.post("/api/admin/capability-cache/evict", params={"scope": "all"})
        assert resp.status_code == 200
        assert resp.json()["evicted"] == 5

    def test_evict_entry_needs_table_and_tab(self, admin_client):
        resp = admin_client.post("/api/admin/capability-cache/evict", params={"scope": "entry"})
        assert resp.status_code == 400

    def test_evict_table_admin_ok(self, admin_client):
        with patch("backend.server.capability_cache.CapabilityCache.evict_table", return_value=4):
            resp = admin_client.post("/api/admin/capability-cache/evict", params={
                "scope": "table", "table_fqn": "c.s.t"})
        assert resp.status_code == 200
        assert resp.json()["evicted"] == 4

    def test_invalid_scope_400(self, admin_client):
        resp = admin_client.post("/api/admin/capability-cache/evict", params={"scope": "bogus"})
        assert resp.status_code == 400
