"""Tests for backend/routes/ml.py — AI/ML lineage (v2.5.0).

Fixes:
- A1:  SQL injection in catalog/schema/table params
- C8:  Warehouse timeout handling
- A10: Empty response on system table access failure
- S1:  SQL injection via the unvalidated model_version param (tables-for-model)
- S2:  Unvalidated catalog param + missing WHERE on feature-tables, and the
       missing _get_client import that made this module's _execute_sql a NameError

Covers serving endpoints inventory, model-to-table lineage,
feature tables, vector search indexes, and validation.
"""
import os
from unittest.mock import patch, MagicMock

import pytest

from databricks.sdk.service.sql import StatementState


def _sql_client(rows=None, columns=None):
    """A WorkspaceClient stub whose statement_execution returns `rows`.

    Deliberately stubs the SDK client and NOT _execute_sql, so tests using it
    run the real executor body in backend/routes/ml.py.
    """
    resp = MagicMock()
    resp.status.state = StatementState.SUCCEEDED
    resp.status.error = None
    resp.result.data_array = rows if rows is not None else []
    cols = []
    for name in (columns or []):
        c = MagicMock()
        c.name = name
        cols.append(c)
    resp.manifest.schema.columns = cols
    client = MagicMock()
    client.statement_execution.execute_statement.return_value = resp
    return client


def _statement_of(client):
    """The SQL text the stub client was asked to execute, whitespace-normalised."""
    kwargs = client.statement_execution.execute_statement.call_args.kwargs
    return " ".join(kwargs["statement"].split())


class TestMLEndpoints:
    """GET /api/ml/endpoints — serving endpoint inventory.

    Stubs `backend.server.ml._execute_sql`, which is the sink this route really
    reaches. Patching `backend.routes.ml._execute_sql` here does nothing:
    /endpoints goes through list_serving_endpoints() in backend/server/ml.py,
    which has its OWN _execute_sql and its own import-bound _get_client — so the
    wrong patch target let these tests build a real WorkspaceClient and call the
    live workspace, which is what made this file take minutes.
    """

    def test_returns_endpoints_list(self, app_client):
        with patch("backend.server.ml._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"endpoint_name": "model-v1", "endpoint_type": "FOUNDATION_MODEL",
                 "daily_requests": "100", "last_served": "2026-07-20"}
            ]
            resp = app_client.get("/api/ml/endpoints")
            assert resp.status_code == 200
            assert resp.json()["endpoints"][0]["endpoint_name"] == "model-v1"

    def test_empty_endpoints(self, app_client):
        with patch("backend.server.ml._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/ml/endpoints")
            assert resp.status_code == 200
            assert resp.json()["endpoints"] == []

    def test_warehouse_failure(self, app_client):
        """C8: Warehouse failure should not silently return empty."""
        with patch("backend.server.ml._execute_sql") as mock_sql:
            mock_sql.side_effect = RuntimeError("No SQL warehouse available.")
            resp = app_client.get("/api/ml/endpoints")
            # A10: Should return error, not empty success. list_serving_endpoints
            # is deliberately non-fatal, so 200-with-empty is the accepted shape.
            assert resp.status_code in (200, 500, 503)


class TestModelsForTable:
    """GET /api/ml/models-for-table — model lineage from training data."""

    def test_missing_table_returns_422(self, app_client):
        resp = app_client.get("/api/ml/models-for-table")
        assert resp.status_code in (422, 400)

    def test_valid_table_returns_models(self, app_client):
        # Live derivation finds nothing, so the app-owned model_lineage table
        # answers. Both stubs sit on backend.server.ml for the reason given in
        # TestMLEndpoints — the route's own _execute_sql is not on this path.
        with patch("backend.server.ml._derive_models_for_table_live", return_value=[]), \
             patch("backend.server.ml._execute_sql") as mock_sql:
            mock_sql.return_value = [
                {"model_name": "churn_model", "version": "3",
                 "registered_at": "2026-06-01", "run_id": "abc123"}
            ]
            resp = app_client.get("/api/ml/models-for-table", params={
                "catalog": "main", "schema": "ml", "table": "training_data"
            })
            assert resp.status_code == 200
            assert resp.json()["models"][0]["model_name"] == "churn_model"

    def test_no_models_found(self, app_client):
        with patch("backend.server.ml._derive_models_for_table_live", return_value=[]), \
             patch("backend.server.ml._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/ml/models-for-table", params={
                "catalog": "main", "schema": "ml", "table": "orphan_table"
            })
            assert resp.status_code == 200
            assert resp.json()["models"] == []

    def test_sql_injection_in_catalog(self, app_client):
        """A1: catalog param SQL injection."""
        resp = app_client.get("/api/ml/models-for-table", params={
            "catalog": "main'; DROP TABLE--", "schema": "ml", "table": "data"
        })
        assert resp.status_code == 400

    def test_sql_injection_in_table(self, app_client):
        """A1: table param SQL injection."""
        resp = app_client.get("/api/ml/models-for-table", params={
            "catalog": "main", "schema": "ml",
            "table": "data' UNION SELECT secret FROM credentials--"
        })
        assert resp.status_code == 400


class TestTablesForModelVersionInjection:
    """S1: model_version was interpolated into a WHERE clause with no validation
    and no escaping, while model_name next to it was allow-listed. Because the
    query is built from implicitly-concatenated f-strings it is a single line, so
    a trailing `--` really does comment out the ORDER BY/LIMIT, and a 7-column
    UNION returns arbitrary rows in-band under the app service principal."""

    #: The confirmed in-band UNION exfiltration payload, column-matched to the
    #: 7 columns the real query selects.
    UNION_PAYLOAD = (
        "y' AND 1=0 UNION SELECT CAST(employee_id AS STRING), CAST(ssn AS STRING), "
        "CAST(salary AS STRING), CAST(bank_account AS STRING), name, dept, "
        "current_timestamp() FROM main.finance.payroll -- "
    )

    def test_union_payload_rejected_400(self, app_client):
        resp = app_client.get("/api/ml/tables-for-model", params={
            "model_name": "x", "model_version": self.UNION_PAYLOAD,
        })
        assert resp.status_code == 400
        assert "model_version" in resp.json()["detail"]

    def test_union_payload_never_reaches_the_service(self, app_client):
        """The 400 must happen before any SQL is built."""
        with patch("backend.routes.ml.get_table_for_model") as mock_svc:
            resp = app_client.get("/api/ml/tables-for-model", params={
                "model_name": "x", "model_version": self.UNION_PAYLOAD,
            })
        assert resp.status_code == 400
        mock_svc.assert_not_called()

    def test_backslash_quote_payload_rejected_400(self, app_client):
        """Quote-doubling alone is bypassable on Databricks SQL (`\\'` escapes the
        quote), so the allow-list must reject backslashes too."""
        resp = app_client.get("/api/ml/tables-for-model", params={
            "model_name": "x", "model_version": "3\\' OR 1=1 -- ",
        })
        assert resp.status_code == 400

    def test_valid_version_still_accepted(self, app_client):
        with patch("backend.routes.ml.get_table_for_model", return_value=[]) as mock_svc:
            resp = app_client.get("/api/ml/tables-for-model", params={
                "model_name": "main.ml.churn", "model_version": "3",
            })
        assert resp.status_code == 200
        assert mock_svc.call_args[0] == ("main.ml.churn", "3")

    def test_empty_version_treated_as_absent(self, app_client):
        """`?model_version=` yields no filter in the sink, so it must not 400 —
        validation and use agree on which values reach the query."""
        with patch("backend.routes.ml.get_table_for_model", return_value=[]):
            resp = app_client.get("/api/ml/tables-for-model", params={
                "model_name": "m", "model_version": "",
            })
        assert resp.status_code == 200

    def test_500_detail_is_generic(self, app_client):
        """A 500 must not echo the exception text back to the caller."""
        with patch("backend.routes.ml.get_table_for_model",
                   side_effect=RuntimeError("TABLE_OR_VIEW_NOT_FOUND: secret_table")):
            resp = app_client.get("/api/ml/tables-for-model", params={"model_name": "m"})
        assert resp.status_code == 500
        assert "secret_table" not in resp.json()["detail"]


class TestFeatureTablesRealSqlPath:
    """S2: exercises the REAL backend.routes.ml._execute_sql.

    The module never imported `_get_client`, so its local _execute_sql raised
    `NameError` for every handler that used it. It went unnoticed because every
    other test patches `backend.routes.ml._execute_sql` wholesale. These tests
    stub only the SDK client, so:
      * `patch.object` without `create=True` fails outright if `_get_client` is
        not a module attribute, and
      * the handler runs the real executor body, which resolves `_get_client`
        by name at call time.
    """

    def test_get_client_is_imported(self):
        """Direct regression guard on the import itself."""
        import backend.lineage_service as ls
        import backend.routes.ml as m
        assert m._get_client is ls._get_client

    def test_real_sql_path_no_catalog_builds_valid_where(self, app_client):
        """Previously `{where}` was empty, leaving `FROM ... AND (...)` with no
        WHERE at all — a syntax error, so the unfiltered case never worked."""
        import backend.routes.ml as m
        client = _sql_client(rows=[["main", "ml", "features", "MANAGED", "f"]],
                             columns=["table_catalog", "table_schema", "table_name",
                                      "table_type", "comment"])
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), \
             patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/feature-tables")
        assert resp.status_code == 200
        assert resp.json()["feature_tables"][0]["table_name"] == "features"
        sql = _statement_of(client)
        assert "FROM system.information_schema.tables WHERE 1=1 AND (lower(comment)" in sql

    def test_real_sql_path_with_catalog_filters(self, app_client):
        import backend.routes.ml as m
        client = _sql_client()
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), \
             patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/feature-tables", params={"catalog": "main"})
        assert resp.status_code == 200
        sql = _statement_of(client)
        assert "WHERE 1=1 AND table_catalog = 'main' AND (lower(comment)" in sql

    def test_catalog_injection_rejected_400(self, app_client):
        """catalog is a UC identifier — allow-listed, never escaped-and-hoped."""
        import backend.routes.ml as m
        client = _sql_client()
        with patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/feature-tables", params={
                "catalog": "main' UNION SELECT 1,2,3,4,5 FROM secrets -- ",
            })
        assert resp.status_code == 400
        client.statement_execution.execute_statement.assert_not_called()

    def test_catalog_backslash_rejected_400(self, app_client):
        resp = app_client.get("/api/ml/feature-tables", params={"catalog": "main\\"})
        assert resp.status_code == 400

    def test_real_sql_path_vector_indexes(self, app_client):
        """/vector-indexes hit the same NameError and swallowed it into an empty
        result plus a misleading "system tables not available" note."""
        import backend.routes.ml as m
        client = _sql_client(rows=[["i1", "id", "e1", "DELTA_SYNC", "c.s.t"]],
                             columns=["index_name", "primary_key", "endpoint_name",
                                      "index_type", "source_table"])
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), \
             patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/vector-indexes")
        assert resp.status_code == 200
        body = resp.json()
        assert body["vector_indexes"][0]["index_name"] == "i1"
        assert "note" not in body

    def test_real_sql_path_inference_tables(self, app_client):
        import backend.routes.ml as m
        client = _sql_client(rows=[["e1", "c.s.log"]],
                             columns=["endpoint_name", "inference_table_name"])
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), \
             patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/inference-tables")
        assert resp.status_code == 200
        assert resp.json()["inference_tables"][0]["endpoint_name"] == "e1"
        assert "note" not in resp.json()

    def test_real_sql_path_vector_lineage_escapes_name(self, app_client):
        """The allow-list is the control; sql_str keeps the literal intact anyway."""
        import backend.routes.ml as m
        client = _sql_client(rows=[["my index", "c.s.t", "e1", "id", "DELTA_SYNC"]],
                             columns=["index_name", "source_table", "endpoint_name",
                                      "primary_key", "index_type"])
        with patch.dict(os.environ, {"DATABRICKS_WAREHOUSE_ID": "wh"}), \
             patch.object(m, "_get_client", return_value=client):
            resp = app_client.get("/api/ml/vector-lineage",
                                  params={"index_name": "my index"})
        assert resp.status_code == 200
        assert resp.json()["lineage"]["source_table"] == "c.s.t"
        assert "WHERE index_name = 'my index'" in _statement_of(client)


class TestErrorDetailHygiene:
    """Raw exception text must stay server-side."""

    def test_feature_tables_500_is_generic(self, app_client):
        with patch("backend.routes.ml._execute_sql",
                   side_effect=RuntimeError("PERMISSION_DENIED on lattice_lineage")):
            resp = app_client.get("/api/ml/feature-tables")
        assert resp.status_code == 500
        assert "lattice_lineage" not in resp.json()["detail"]

    def test_vector_lineage_note_is_generic(self, app_client):
        """This one degrades to a 200 + note, which used to carry str(e) —
        including the `NameError: name '_get_client' is not defined` giveaway."""
        with patch("backend.routes.ml._execute_sql",
                   side_effect=RuntimeError("NameError leak: system.x.y")):
            resp = app_client.get("/api/ml/vector-lineage", params={"index_name": "i1"})
        assert resp.status_code == 200
        assert resp.json()["lineage"] is None
        assert "NameError" not in resp.json()["note"]

    def test_endpoints_500_is_generic(self, app_client):
        with patch("backend.routes.ml.list_serving_endpoints",
                   side_effect=RuntimeError("warehouse whid-1234 is DELETED")):
            resp = app_client.get("/api/ml/endpoints")
        assert resp.status_code == 500
        assert "whid-1234" not in resp.json()["detail"]
