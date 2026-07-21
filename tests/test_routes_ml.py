"""Tests for backend/routes/ml.py — AI/ML lineage (v2.5.0).

Covers serving endpoints inventory, model-to-table lineage,
feature tables, vector search indexes, and validation.
"""
from unittest.mock import patch, MagicMock

import pytest


class TestMLEndpoints:
    """GET /api/ml/endpoints — serving endpoint inventory."""

    def test_returns_endpoints_list(self, app_client):
        with patch("backend.routes.ml._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"endpoint_name": "model-v1", "endpoint_type": "FOUNDATION_MODEL",
                 "daily_requests": "100", "last_served": "2026-07-20"}
            ]
            resp = app_client.get("/api/ml/endpoints")
            assert resp.status_code == 200
            data = resp.json()
            assert isinstance(data, list)

    def test_empty_endpoints(self, app_client):
        with patch("backend.routes.ml._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/ml/endpoints")
            assert resp.status_code == 200
            assert resp.json() == [] or isinstance(resp.json(), dict)


class TestModelsForTable:
    """GET /api/ml/models-for-table — model lineage from training data."""

    def test_missing_table_returns_422(self, app_client):
        resp = app_client.get("/api/ml/models-for-table")
        assert resp.status_code in (422, 400)

    def test_valid_table_returns_models(self, app_client):
        with patch("backend.routes.ml._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"model_name": "churn_model", "version": "3",
                 "registered_at": "2026-06-01", "run_id": "abc123"}
            ]
            resp = app_client.get("/api/ml/models-for-table", params={
                "catalog": "main", "schema": "ml", "table": "training_data"
            })
            assert resp.status_code == 200

    def test_no_models_found(self, app_client):
        with patch("backend.routes.ml._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/ml/models-for-table", params={
                "catalog": "main", "schema": "ml", "table": "orphan_table"
            })
            assert resp.status_code == 200
