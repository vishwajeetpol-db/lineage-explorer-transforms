"""Coverage-focused tests for backend/routes/dq.py.

Drives the DQ rule CRUD paths, live metrics (permission preflight, per-rule
evaluation across NOT_NULL/UNIQUE/RANGE/REGEX/CUSTOM), quality propagation,
plus the internal helpers _execute_sql, _validate_expression, _build_check_sql
and _score_to_grade. No backend/ edits; everything mocked; fast + offline.
"""
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_sql():
    with patch("backend.routes.dq._execute_sql") as m:
        m.return_value = []
        yield m


class TestExecuteSql:
    def test_no_warehouse(self):
        import backend.routes.dq as d
        with patch.object(d, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                d._execute_sql("SELECT 1")

    def test_success_rows(self):
        import backend.routes.dq as d
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["v1", "v2"]]
        col_a, col_b = MagicMock(), MagicMock()
        col_a.name, col_b.name = "a", "b"
        resp.manifest.schema.columns = [col_a, col_b]
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(d, "WAREHOUSE_ID", "wh"), patch.object(
            d, "_get_client", return_value=client
        ):
            assert d._execute_sql("SELECT a, b") == [{"a": "v1", "b": "v2"}]

    def test_empty_result(self):
        import backend.routes.dq as d
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result = None
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(d, "WAREHOUSE_ID", "wh"), patch.object(
            d, "_get_client", return_value=client
        ):
            assert d._execute_sql("SELECT 1") == []

    def test_failed_state(self):
        import backend.routes.dq as d
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.FAILED
        resp.status.error.message = "bad"
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(d, "WAREHOUSE_ID", "wh"), patch.object(
            d, "_get_client", return_value=client
        ):
            with pytest.raises(RuntimeError, match="SQL failed"):
                d._execute_sql("SELECT 1")

    def test_client_exception_wrapped(self):
        import backend.routes.dq as d

        client = MagicMock()
        client.statement_execution.execute_statement.side_effect = ValueError("boom")
        with patch.object(d, "WAREHOUSE_ID", "wh"), patch.object(
            d, "_get_client", return_value=client
        ):
            with pytest.raises(RuntimeError, match="SQL failed"):
                d._execute_sql("SELECT 1")

    def test_ensure_dq_table_swallows_error(self):
        import backend.routes.dq as d
        with patch.object(d, "_execute_sql", side_effect=RuntimeError("nope")):
            d._ensure_dq_table()  # should not raise


class TestListRules:
    def test_list_all_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"rule_id": "r1", "table_fqn": "c.s.t"}]
        resp = app_client.get("/api/dq-rules")
        assert resp.status_code == 200
        assert resp.json()["rules"]

    def test_list_filtered_ok(self, app_client, mock_sql):
        resp = app_client.get("/api/dq-rules", params={"table_fqn": "c.s.t"})
        assert resp.status_code == 200

    def test_list_bad_fqn_400(self, app_client, mock_sql):
        resp = app_client.get("/api/dq-rules", params={"table_fqn": "bad fqn"})
        assert resp.status_code == 400

    def test_list_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/dq-rules")
        assert resp.status_code == 500


class TestColumns:
    def test_ok(self, app_client, mock_sql):
        mock_sql.return_value = [
            {"column_name": "c1", "rule_type": "NOT_NULL"},
            {"column_name": None, "rule_type": "CUSTOM"},
        ]
        resp = app_client.get(
            "/api/dq-rules/columns",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["table_fqn"] == "c.s.t"
        assert "__table__" in data["columns"]

    def test_missing_params_422(self, app_client, mock_sql):
        resp = app_client.get("/api/dq-rules/columns", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_bad_identifier_400(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/dq-rules/columns",
            params={"catalog": "c;DROP", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get(
            "/api/dq-rules/columns",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 500


class TestUpsert:
    def test_non_admin_403(self, non_admin_client, mock_sql):
        resp = non_admin_client.post(
            "/api/dq-rules",
            json={"table_fqn": "c.s.t", "expression": "x IS NOT NULL"},
        )
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client, mock_sql):
        resp = admin_client.post(
            "/api/dq-rules",
            json={
                "table_fqn": "c.s.t",
                "column_name": "x",
                "rule_type": "NOT_NULL",
                "expression": "x IS NOT NULL",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "upserted"

    def test_admin_with_rule_id(self, admin_client, mock_sql):
        resp = admin_client.post(
            "/api/dq-rules",
            json={"rule_id": "fixed", "table_fqn": "c.s.t", "expression": "x IS NOT NULL"},
        )
        assert resp.status_code == 200
        assert resp.json()["rule_id"] == "fixed"

    def test_missing_body_422(self, admin_client, mock_sql):
        resp = admin_client.post("/api/dq-rules")
        assert resp.status_code == 422

    def test_bad_fqn_400(self, admin_client, mock_sql):
        resp = admin_client.post(
            "/api/dq-rules", json={"table_fqn": "bad", "expression": "x IS NOT NULL"}
        )
        assert resp.status_code == 400

    def test_injection_expression_400(self, admin_client, mock_sql):
        resp = admin_client.post(
            "/api/dq-rules",
            json={
                "table_fqn": "c.s.t",
                "rule_type": "CUSTOM",
                "expression": "1=1 UNION SELECT * FROM information_schema.tables",
            },
        )
        assert resp.status_code == 400

    def test_error_500(self, admin_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = admin_client.post(
            "/api/dq-rules",
            json={"table_fqn": "c.s.t", "expression": "x IS NOT NULL"},
        )
        assert resp.status_code == 500


class TestDelete:
    def test_non_admin_403(self, non_admin_client, mock_sql):
        resp = non_admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 403

    def test_admin_ok(self, admin_client, mock_sql):
        resp = admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_error_500(self, admin_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 500


class TestMetrics:
    def test_missing_param_422(self, app_client, mock_sql):
        resp = app_client.get("/api/dq-rules/metrics")
        assert resp.status_code == 422

    def test_bad_fqn_400(self, app_client, mock_sql):
        resp = app_client.get("/api/dq-rules/metrics", params={"table_fqn": "bad;x"})
        assert resp.status_code == 400

    def test_permission_denied_403(self, app_client, mock_sql):
        mock_sql.side_effect = Exception("INSUFFICIENT_PERMISSIONS on table")
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 403

    def test_table_not_found_404(self, app_client, mock_sql):
        mock_sql.side_effect = Exception("TABLE_OR_VIEW_NOT_FOUND: c.s.t")
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 404

    def test_no_rules(self, app_client, mock_sql):
        # preflight ok, ensure ok, rules query returns []
        mock_sql.return_value = []
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "No DQ rules defined"

    def test_full_evaluation(self, app_client, mock_sql):
        rules = [
            {"rule_id": "r1", "column_name": "x", "rule_type": "NOT_NULL",
             "expression": "", "severity": "ERROR"},
            {"rule_id": "r2", "column_name": "y", "rule_type": "CUSTOM",
             "expression": "y > 0", "severity": "WARN"},
            {"rule_id": "r3", "column_name": "", "rule_type": "CUSTOM",
             "expression": "", "severity": "ERROR"},  # unbuildable -> skipped
        ]

        def _se(sql):
            if "LIMIT 0" in sql:
                return []  # preflight
            if "CREATE TABLE" in sql:
                return []
            if sql.strip().startswith("SELECT * FROM") and "dq_rules" in sql:
                return rules
            if "total_rows" in sql:
                return [{"total_rows": 100, "passing_rows": 99}]
            return []

        mock_sql.side_effect = _se
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules_total"] == 3
        statuses = {m["rule_id"]: m["status"] for m in data["metrics"]}
        assert statuses["r3"] == "skipped"

    def test_rule_eval_no_data_and_error(self, app_client, mock_sql):
        rules = [
            {"rule_id": "r1", "column_name": "x", "rule_type": "NOT_NULL",
             "expression": "", "severity": "ERROR"},
            {"rule_id": "r2", "column_name": "z", "rule_type": "NOT_NULL",
             "expression": "", "severity": "ERROR"},
        ]

        def _se(sql):
            if "LIMIT 0" in sql:
                return []
            if "CREATE TABLE" in sql:
                return []
            if sql.strip().startswith("SELECT * FROM") and "dq_rules" in sql:
                return rules
            if "`x`" in sql:
                return []  # no_data branch
            if "`z`" in sql:
                raise RuntimeError("eval failed")  # error branch
            return []

        mock_sql.side_effect = _se
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        statuses = {m["rule_id"]: m["status"] for m in resp.json()["metrics"]}
        assert statuses["r1"] == "no_data"
        assert statuses["r2"] == "error"

    def test_error_500(self, app_client, mock_sql):
        def _se(sql):
            if "LIMIT 0" in sql:
                return []
            raise RuntimeError("boom")

        mock_sql.side_effect = _se
        resp = app_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 500


class TestPropagation:
    def test_ok(self, app_client, mock_sql):
        def _se(sql):
            if "system.access.table_lineage" in sql:
                return [{"source_table_full_name": "c.s.up"},
                        {"source_table_full_name": ""}]
            if "COUNT(*) as cnt" in sql:
                return [{"cnt": 2}]
            return []

        mock_sql.side_effect = _se
        resp = app_client.get(
            "/api/dq-rules/propagation",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["upstream_count"] == 1
        assert data["covered_count"] == 1

    def test_bad_identifier_400(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/dq-rules/propagation",
            params={"catalog": "c;x", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 400

    def test_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get(
            "/api/dq-rules/propagation",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 500


class TestValidateExpression:
    def test_empty_ok(self):
        import backend.routes.dq as d
        assert d._validate_expression("", "CUSTOM") == ""

    def test_too_long_400(self):
        import backend.routes.dq as d
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            d._validate_expression("x" * 501, "CUSTOM")

    def test_injection_400(self):
        import backend.routes.dq as d
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            d._validate_expression("a; DROP TABLE t", "CUSTOM")

    def test_unbalanced_parens_400(self):
        import backend.routes.dq as d
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            d._validate_expression("(a > 1", "CUSTOM")

    def test_valid(self):
        import backend.routes.dq as d
        assert d._validate_expression("(a > 1)", "CUSTOM") == "(a > 1)"


class TestBuildCheckSql:
    def test_not_null(self):
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "NOT_NULL", "", 100)
        assert "IS NOT NULL" in sql

    def test_unique(self):
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "UNIQUE", "", 100)
        assert "COUNT(DISTINCT" in sql

    def test_range_ok(self):
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "RANGE", "1,10", 100)
        assert "BETWEEN 1.0 AND 10.0" in sql

    def test_range_non_numeric_none(self):
        import backend.routes.dq as d
        assert d._build_check_sql("c.s.t", "col", "RANGE", "a,b", 100) is None

    def test_range_wrong_parts_none(self):
        import backend.routes.dq as d
        assert d._build_check_sql("c.s.t", "col", "RANGE", "1", 100) is None

    def test_regex(self):
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "REGEX", "^a$", 100)
        assert "RLIKE" in sql

    def test_custom(self):
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "CUSTOM", "col > 0", 100)
        assert "CASE WHEN (col > 0)" in sql

    def test_unknown_none(self):
        import backend.routes.dq as d
        assert d._build_check_sql("c.s.t", "col", "OTHER", "", 100) is None


class TestScoreToGrade:
    @pytest.mark.parametrize(
        "score,grade",
        [(None, None), (0.995, "A"), (0.96, "B"), (0.92, "C"), (0.85, "D"), (0.5, "F")],
    )
    def test_grades(self, score, grade):
        import backend.routes.dq as d
        assert d._score_to_grade(score) == grade
