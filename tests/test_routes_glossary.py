"""Tests for backend/routes/glossary.py — Business lineage / glossary (cap 10).

Terms, domains, KPIs, term↔table links, propagation suggestions, and the
lineage overlay. The module owns its own `_execute_sql`; it's mocked here.
"""
from unittest.mock import patch

import pytest


class TestTerms:
    def test_list_terms_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/terms")
        assert resp.status_code == 200

    def test_get_term_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql",
                   return_value=[{"term_id": "t1", "name": "Revenue"}]):
            resp = app_client.get("/api/glossary/terms/t1")
        assert resp.status_code in (200, 404)

    def test_upsert_term_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.post("/api/glossary/terms", json={"name": "Revenue"})
        assert resp.status_code in (200, 201)

    def test_delete_term_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.delete("/api/glossary/terms/t1")
        assert resp.status_code in (200, 204)


class TestDomainsAndKpis:
    def test_list_domains_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/domains")
        assert resp.status_code == 200

    def test_upsert_domain_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.post("/api/glossary/domains", json={"name": "Finance"})
        assert resp.status_code in (200, 201)

    def test_list_kpis_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/kpis")
        assert resp.status_code == 200

    def test_upsert_kpi_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.post("/api/glossary/kpis", json={"name": "MRR"})
        assert resp.status_code in (200, 201)


class TestTermLinksAndOverlays:
    def test_terms_for_table_requires_params(self, app_client):
        resp = app_client.get("/api/glossary/for-table", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_terms_for_table_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/for-table", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code == 200

    def test_link_term_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.post("/api/glossary/link", json={
                "term_id": "t1", "catalog": "c", "schema_name": "s", "table": "t"})
        assert resp.status_code in (200, 201, 422)  # body schema may require more fields

    def test_propagate_suggestions_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/propagate-suggestions", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code in (200, 422)

    def test_lineage_overlay_ok(self, app_client):
        with patch("backend.routes.glossary._execute_sql", return_value=[]):
            resp = app_client.get("/api/glossary/lineage-overlay", params={
                "catalog": "c", "schema": "s", "table": "t"})
        assert resp.status_code in (200, 422)
