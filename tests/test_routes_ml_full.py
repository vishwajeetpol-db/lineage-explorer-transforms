"""Coverage-focused tests for backend/routes/ml.py.

Drives the serving-endpoint inventory, usage, models-for-table,
tables-for-model, register-lineage paths (mocking backend.server.ml.*),
plus the extension endpoints (feature-tables, vector-indexes, vector-lineage,
prompt-lineage, inference-tables) which use the module-level _execute_sql.
No backend/ edits; everything mocked; fast + offline.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_sql():
    with patch("backend.routes.ml._execute_sql") as m:
        m.return_value = []
        yield m


class TestServingEndpoints:
    def test_ok(self, app_client):
        with patch("backend.routes.ml.list_serving_endpoints", return_value=[{"name": "e1"}]):
            resp = app_client.get("/api/ml/endpoints")
        assert resp.status_code == 200
        assert resp.json()["endpoints"]

    def test_error_500(self, app_client):
        with patch("backend.routes.ml.list_serving_endpoints", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/ml/endpoints")
        assert resp.status_code == 500


class TestEndpointUsage:
    def test_ok(self, app_client):
        with patch("backend.routes.ml.get_endpoint_usage", return_value=[{"day": "2026-01-01"}]):
            resp = app_client.get("/api/ml/endpoints/my-endpoint/usage")
        assert resp.status_code == 200
        assert resp.json()["usage"]

    def test_invalid_name_400(self, app_client):
        resp = app_client.get("/api/ml/endpoints/bad;name/usage")
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.ml.get_endpoint_usage", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/ml/endpoints/e1/usage")
        assert resp.status_code == 500


class TestModelsForTable:
    def test_ok(self, app_client):
        with patch("backend.routes.ml.get_models_for_table", return_value=[{"model": "m1"}]):
            resp = app_client.get(
                "/api/ml/models-for-table",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 200
        assert resp.json()["models"]

    def test_missing_params_422(self, app_client):
        resp = app_client.get("/api/ml/models-for-table", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_bad_identifier_400(self, app_client):
        resp = app_client.get(
            "/api/ml/models-for-table",
            params={"catalog": "c;x", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.ml.get_models_for_table", side_effect=RuntimeError("boom")):
            resp = app_client.get(
                "/api/ml/models-for-table",
                params={"catalog": "c", "schema": "s", "table": "t"},
            )
        assert resp.status_code == 500


class TestTablesForModel:
    def test_ok(self, app_client):
        with patch("backend.routes.ml.get_table_for_model", return_value=[{"table": "c.s.t"}]):
            resp = app_client.get(
                "/api/ml/tables-for-model",
                params={"model_name": "my.model", "model_version": "3"},
            )
        assert resp.status_code == 200
        assert resp.json()["tables"]

    def test_missing_param_422(self, app_client):
        resp = app_client.get("/api/ml/tables-for-model")
        assert resp.status_code == 422

    def test_invalid_name_400(self, app_client):
        resp = app_client.get("/api/ml/tables-for-model", params={"model_name": "bad;name"})
        assert resp.status_code == 400

    def test_error_500(self, app_client):
        with patch("backend.routes.ml.get_table_for_model", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/ml/tables-for-model", params={"model_name": "m1"})
        assert resp.status_code == 500


class TestRegisterLineage:
    def test_ok(self, app_client):
        with patch("backend.routes.ml.register_model_lineage", return_value={"status": "ok"}):
            resp = app_client.post(
                "/api/ml/register-lineage",
                json={
                    "model_name": "m1",
                    "model_version": "1",
                    "training_table": "c.s.t",
                },
            )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_missing_body_422(self, app_client):
        resp = app_client.post("/api/ml/register-lineage", json={"model_name": "m1"})
        assert resp.status_code == 422

    def test_error_500(self, app_client):
        with patch("backend.routes.ml.register_model_lineage", side_effect=RuntimeError("boom")):
            resp = app_client.post(
                "/api/ml/register-lineage",
                json={"model_name": "m1", "model_version": "1", "training_table": "c.s.t"},
            )
        assert resp.status_code == 500


class TestFeatureTables:
    def test_ok_no_catalog(self, app_client, mock_sql):
        mock_sql.return_value = [{"table_name": "features"}]
        resp = app_client.get("/api/ml/feature-tables")
        assert resp.status_code == 200
        assert resp.json()["feature_tables"]

    def test_ok_with_catalog(self, app_client, mock_sql):
        resp = app_client.get("/api/ml/feature-tables", params={"catalog": "c"})
        assert resp.status_code == 200

    def test_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/ml/feature-tables")
        assert resp.status_code == 500


class TestVectorIndexes:
    def test_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"index_name": "i1"}]
        resp = app_client.get("/api/ml/vector-indexes")
        assert resp.status_code == 200
        assert resp.json()["vector_indexes"]

    def test_missing_system_table_note(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("no table")
        resp = app_client.get("/api/ml/vector-indexes")
        assert resp.status_code == 200
        assert "note" in resp.json()


class TestVectorLineage:
    def test_ok(self, app_client, mock_sql):
        mock_sql.return_value = [
            {"index_name": "i1", "source_table": "c.s.t", "endpoint_name": "e1",
             "index_type": "DELTA_SYNC", "primary_key": "id"}
        ]
        resp = app_client.get("/api/ml/vector-lineage", params={"index_name": "i1"})
        assert resp.status_code == 200
        assert resp.json()["lineage"]["source_table"] == "c.s.t"

    def test_not_found(self, app_client, mock_sql):
        mock_sql.return_value = []
        resp = app_client.get("/api/ml/vector-lineage", params={"index_name": "i1"})
        assert resp.status_code == 200
        assert resp.json()["lineage"] is None

    def test_invalid_name_400(self, app_client, mock_sql):
        resp = app_client.get("/api/ml/vector-lineage", params={"index_name": "bad;x"})
        assert resp.status_code == 400

    def test_sql_error_returns_note(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/ml/vector-lineage", params={"index_name": "i1"})
        assert resp.status_code == 200
        assert resp.json()["lineage"] is None


class TestPromptLineage:
    def test_ok_rag(self, app_client, mock_sql):
        mock_sql.return_value = [{"index_name": "i1", "source_table": "c.s.t"}]
        with patch("backend.routes.ml.get_models_for_table", return_value=[]), patch(
            "backend.server.ml.get_endpoint_usage", return_value=[]
        ):
            resp = app_client.get("/api/ml/prompt-lineage", params={"endpoint_name": "e1"})
        assert resp.status_code == 200
        assert resp.json()["lineage_type"] == "rag"

    def test_ok_model_serving(self, app_client, mock_sql):
        # vector index query raises (swallowed) -> vector_sources empty -> model_serving
        mock_sql.side_effect = RuntimeError("no vector table")
        with patch("backend.routes.ml.get_models_for_table", return_value=[]), patch(
            "backend.server.ml.get_endpoint_usage", return_value=[]
        ):
            resp = app_client.get("/api/ml/prompt-lineage", params={"endpoint_name": "e1"})
        assert resp.status_code == 200
        assert resp.json()["lineage_type"] == "model_serving"

    def test_invalid_name_400(self, app_client, mock_sql):
        resp = app_client.get("/api/ml/prompt-lineage", params={"endpoint_name": "bad;x"})
        assert resp.status_code == 400

    def test_error_500(self, app_client, mock_sql):
        # inner vector query swallowed; make get_models_for_table raise to hit outer 500
        with patch("backend.routes.ml.get_models_for_table", side_effect=RuntimeError("boom")):
            resp = app_client.get("/api/ml/prompt-lineage", params={"endpoint_name": "e1"})
        assert resp.status_code == 500


class TestInferenceTables:
    def test_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"endpoint_name": "e1", "inference_table_name": "c.s.log"}]
        resp = app_client.get("/api/ml/inference-tables")
        assert resp.status_code == 200
        assert resp.json()["inference_tables"]

    def test_missing_system_table_note(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("no table")
        resp = app_client.get("/api/ml/inference-tables")
        assert resp.status_code == 200
        assert "note" in resp.json()


class TestExecuteSqlInternal:
    def test_no_warehouse(self):
        import backend.routes.ml as m
        import os
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": ""}):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                m._execute_sql("SELECT 1")

    def test_success_rows(self):
        import backend.routes.ml as m
        import os
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["v1"]]
        col = MagicMock()
        col.name = "a"
        resp.manifest.schema.columns = [col]
        client.statement_execution.execute_statement.return_value = resp
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), patch.object(
            m, "_get_client", create=True, return_value=client
        ):
            assert m._execute_sql("SELECT a") == [{"a": "v1"}]

    def test_empty_result(self):
        import backend.routes.ml as m
        import os
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result = None
        client.statement_execution.execute_statement.return_value = resp
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), patch.object(
            m, "_get_client", create=True, return_value=client
        ):
            assert m._execute_sql("SELECT 1") == []

    def test_failed_state(self):
        import backend.routes.ml as m
        import os
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.FAILED
        resp.status.error.message = "bad"
        client.statement_execution.execute_statement.return_value = resp
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), patch.object(
            m, "_get_client", create=True, return_value=client
        ):
            with pytest.raises(RuntimeError, match="SQL failed"):
                m._execute_sql("SELECT 1")
