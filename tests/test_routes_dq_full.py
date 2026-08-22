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
        # The handler confirms the row exists before reporting success.
        mock_sql.return_value = [{"1": 1}]
        resp = admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_missing_rule_404(self, admin_client, mock_sql):
        mock_sql.return_value = []
        resp = admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 404

    def test_error_500(self, admin_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = admin_client.delete("/api/dq-rules/r1")
        assert resp.status_code == 500
        # the warehouse message must not reach the client
        assert "boom" not in resp.text


class TestMetrics:
    """A2 FIX: /metrics executes stored expressions, so it is admin-gated —
    every case here needs an admin caller."""

    def test_non_admin_403(self, non_admin_client, mock_sql):
        resp = non_admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 403
        mock_sql.assert_not_called()

    def test_missing_param_422(self, admin_client, mock_sql):
        resp = admin_client.get("/api/dq-rules/metrics")
        assert resp.status_code == 422

    def test_bad_fqn_400(self, admin_client, mock_sql):
        resp = admin_client.get("/api/dq-rules/metrics", params={"table_fqn": "bad;x"})
        assert resp.status_code == 400

    def test_permission_denied_403(self, admin_client, mock_sql):
        mock_sql.side_effect = Exception("INSUFFICIENT_PERMISSIONS on table")
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 403

    def test_table_not_found_404(self, admin_client, mock_sql):
        mock_sql.side_effect = Exception("TABLE_OR_VIEW_NOT_FOUND: c.s.t")
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 404

    def test_no_rules(self, admin_client, mock_sql):
        # preflight ok, ensure ok, rules query returns []
        mock_sql.return_value = []
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "No DQ rules defined"

    def test_full_evaluation(self, admin_client, mock_sql):
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
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["rules_total"] == 3
        statuses = {m["rule_id"]: m["status"] for m in data["metrics"]}
        assert statuses["r3"] == "skipped"

    def test_rule_eval_no_data_and_error(self, admin_client, mock_sql):
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
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        statuses = {m["rule_id"]: m["status"] for m in resp.json()["metrics"]}
        assert statuses["r1"] == "no_data"
        assert statuses["r2"] == "error"

    def test_legacy_invalid_expression_marked_not_executed(self, admin_client, mock_sql):
        """A1 FIX: a rule stored before the allow-list existed (second-order
        injection) is reported as `invalid` and never executed — and it does not
        take the rest of the panel down with it."""
        rules = [
            {"rule_id": "evil", "column_name": "id", "rule_type": "CUSTOM",
             "expression": "1=1) UNION SELECT secret FROM credentials WHERE (1=1",
             "severity": "ERROR"},
            {"rule_id": "ok", "column_name": "x", "rule_type": "NOT_NULL",
             "expression": "", "severity": "ERROR"},
        ]

        def _se(sql):
            assert "UNION SELECT secret" not in sql  # never reaches the warehouse
            if "LIMIT 0" in sql or "CREATE TABLE" in sql:
                return []
            if sql.strip().startswith("SELECT * FROM") and "dq_rules" in sql:
                return rules
            if "total_rows" in sql:
                return [{"total_rows": 10, "passing_rows": 10}]
            return []

        mock_sql.side_effect = _se
        resp = admin_client.get(
            "/api/dq-rules/metrics", params={"table_fqn": "c.s.t"}
        )
        assert resp.status_code == 200
        statuses = {m["rule_id"]: m["status"] for m in resp.json()["metrics"]}
        assert statuses["evil"] == "invalid"
        assert statuses["ok"] == "pass"

    def test_error_500(self, admin_client, mock_sql):
        def _se(sql):
            if "LIMIT 0" in sql:
                return []
            raise RuntimeError("boom")

        mock_sql.side_effect = _se
        resp = admin_client.get(
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
    """The CUSTOM expression check is an ALLOW-list (positive grammar), not a
    deny-list: a CUSTOM expression is interpolated at SQL *code* position by
    _build_check_sql, where only an enumerated grammar is sound."""

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

    def test_scalar_subquery_rejected(self):
        """The read-oracle payload: needs no quotes and matched none of the old
        deny-list patterns, but names SELECT/FROM/WHERE."""
        import backend.routes.dq as d
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            d._validate_expression(
                "(SELECT count(*) FROM main.hr.payroll WHERE salary > 250000) > 0",
                "CUSTOM",
            )
        assert exc.value.status_code == 400
        assert "SELECT" in exc.value.detail

    @pytest.mark.parametrize(
        "expr",
        [
            "amount > 0 OR EXISTS (x)",           # EXISTS opens a subquery
            "a = 1 /* comment */ AND b = 2",      # block comment
            "a = 1 -- trailing",                  # line comment
            "a = 1; SET spark.x = 1",             # statement separator
            "my_udf(col) > 0",                    # non-allow-listed function
            "col & 1 = 1",                        # character outside the grammar
            "col = 'unterminated",                # literal swallows the statement
            "col = 'a\\'",                        # backslash escape ambiguity
            "a UNION ALL b",
            "a JOIN b",
            "WITH x AS (a) a",
        ],
    )
    def test_rejected_expressions(self, expr):
        import backend.routes.dq as d
        from fastapi import HTTPException
        with pytest.raises(HTTPException):
            d._validate_expression(expr, "CUSTOM")

    @pytest.mark.parametrize(
        "expr",
        [
            "amount > 0 AND status IN ('active','pending')",  # the everyday rule
            "x IS NOT NULL",
            "amount BETWEEN 1 AND 10",
            "upper(trim(name)) LIKE 'A%'",
            "a > 1 AND (b < 2 OR c = 3)",
            "note = 'it''s fine'",                            # escaped quote
            "cast(x AS int) > 0",
            "`odd column` <> 'x'",
            "abs(delta) / 100 <= 0.5",
        ],
    )
    def test_accepted_expressions(self, expr):
        """The other direction: ordinary DQ rules must keep working."""
        import backend.routes.dq as d
        assert d._validate_expression(expr, "CUSTOM") == expr

    def test_regex_rule_keeps_regex_syntax(self):
        """REGEX expressions land inside a quoted literal, so real regex
        metacharacters must survive."""
        import backend.routes.dq as d
        assert d._validate_expression(r"^[0-9]{3}-[0-9]{4}$", "REGEX") == r"^[0-9]{3}-[0-9]{4}$"

    @pytest.mark.parametrize(
        "expr",
        [
            "^.*/v1/.*$",            # `*/` — a block-comment token
            "^[0-9]{4}--[0-9]{2}$",  # `--` — a line-comment token
            "^(select|update)$",     # bare SQL words
            r"\d{3}-\d{4}",          # backslash classes
        ],
    )
    def test_regex_metacharacters_are_not_keyword_screened(self, expr):
        """These are all legitimate patterns that the old keyword/comment screen
        rejected. At a literal position the escaping is the control, so screening
        keywords on top only broke real regexes — see
        test_regex_payload_is_neutralised_by_escaping for the property that
        actually holds."""
        import backend.routes.dq as d
        assert d._validate_expression(expr, "REGEX") == expr

    def test_regex_payload_is_neutralised_by_escaping(self):
        """The former keyword screen rejected this outright. It is now accepted
        and rendered inert instead: sql_str escapes it into the RLIKE literal, so
        no part of it can reach a code position. This is the same guarantee
        TestBuildCheckSql.test_regex_literal_is_escaped pins for `\\'`."""
        import backend.routes.dq as d
        payload = "x' UNION SELECT 1 --"
        assert d._validate_expression(payload, "REGEX") == payload
        sql = d._build_check_sql("c.s.t", "col", "REGEX", payload, 100)
        # the quote is doubled, so UNION SELECT stays inside the literal
        assert "RLIKE 'x'' UNION SELECT 1 --'" in sql
        assert "RLIKE 'x' UNION" not in sql


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

    def test_custom_string_literal_not_quote_doubled(self):
        """The old `expression.replace("'", "''")` at code position corrupted every
        legitimate literal (`status = 'active'` -> `status = ''active''`) while
        protecting nothing — the expression is code, not a literal."""
        import backend.routes.dq as d
        sql = d._build_check_sql(
            "c.s.t", "col", "CUSTOM", "status IN ('active','pending')", 100
        )
        assert "CASE WHEN (status IN ('active','pending'))" in sql
        assert "''active''" not in sql

    def test_regex_literal_is_escaped(self):
        r"""REGEX *is* at literal position, so it is escaped backslash-first: a
        `\'` prefix must not be able to close the RLIKE literal."""
        import backend.routes.dq as d
        sql = d._build_check_sql("c.s.t", "col", "REGEX", "\\'x", 100)
        assert "RLIKE '\\\\''x'" in sql
        assert "RLIKE '\\''" not in sql  # the bypassable quote-only form

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
