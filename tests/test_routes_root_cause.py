"""Tests for backend/routes/root_cause.py — Root Cause Analysis (cap 09).

Covers the four RCA endpoints: column+anomaly /analyze, health-based /trace,
column /upstream-path, and /run-failures. Validation + injection + happy path.
(/trace's cache behavior is exercised in test_capability_cache_routes.py.)
"""
from unittest.mock import patch

import pytest


class TestRootCauseAnalyze:
    def test_requires_column(self, app_client):
        resp = app_client.post("/api/root-cause/analyze", json={
            "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code == 422  # missing required 'column'

    def test_injection_400(self, app_client):
        resp = app_client.post("/api/root-cause/analyze", json={
            "catalog": "bad;", "schema_name": "s", "table": "t", "column": "x"})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.routes.root_cause.trace_root_cause",
                   return_value={"target_table": "c.s.t", "candidates": []}):
            resp = app_client.post("/api/root-cause/analyze", json={
                "catalog": "c", "schema_name": "s", "table": "t", "column": "x"})
        assert resp.status_code == 200
        assert "candidates" in resp.json()


class TestRootCauseTrace:
    def test_requires_params(self, app_client):
        resp = app_client.get("/api/root-cause/trace", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_injection_400(self, app_client):
        resp = app_client.get("/api/root-cause/trace", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.server.capability_cache.CapabilityCache.get", return_value=None), \
             patch("backend.server.capability_cache.CapabilityCache.set", return_value=True), \
             patch("backend.routes.root_cause.trace_root_cause_table",
                   return_value={"focus_table": "c.s.t", "counts": {}, "prime_suspect": None,
                                 "failure_path": [], "flagged": []}):
            resp = app_client.get("/api/root-cause/trace", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200


class TestUpstreamPath:
    def test_requires_column(self, app_client):
        resp = app_client.get("/api/root-cause/upstream-path", params={
            "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 422

    def test_ok(self, app_client):
        with patch("backend.routes.root_cause._walk_upstream_columns", return_value=[]):
            resp = app_client.get("/api/root-cause/upstream-path", params={
                "catalog": "c", "schema": "s", "table": "t", "column": "x"})
        assert resp.status_code == 200


class TestRunFailures:
    def test_injection_400(self, app_client):
        resp = app_client.get("/api/root-cause/run-failures", params={
            "catalog": "bad;", "schema": "s", "table": "t"})
        assert resp.status_code == 400

    def test_ok(self, app_client):
        with patch("backend.routes.root_cause._get_failed_runs_around", return_value=[]):
            resp = app_client.get("/api/root-cause/run-failures", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200
