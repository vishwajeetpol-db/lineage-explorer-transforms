"""Tests for backend.server.governance (capability 17 — governance & classification).

Covers _execute_sql, _ensure_governance_config_table, get_table_governance
(config-rule / tag-rule / heuristic classification branches, degraded fields,
early return when columns unavailable), rule CRUD, and downstream sensitivity
propagation.
"""
from unittest.mock import MagicMock, patch

import pytest
from databricks.sdk.service.sql import StatementState

from backend.server import governance


def _col(name):
    c = MagicMock()
    c.name = name
    return c


def _resp(state=StatementState.SUCCEEDED, columns=None, data=None, error=None):
    resp = MagicMock()
    resp.status.state = state
    resp.status.error = error
    if data is None:
        resp.result = None
    else:
        resp.result.data_array = data
        resp.manifest.schema.columns = [_col(c) for c in (columns or [])]
    return resp


class TestExecuteSql:
    def test_no_warehouse_raises(self):
        with patch.object(governance, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError):
                governance._execute_sql("SELECT 1")

    def test_success(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            columns=["a"], data=[[1]]
        )
        with patch.object(governance, "WAREHOUSE_ID", "wh"), \
             patch.object(governance, "_get_client", return_value=client):
            assert governance._execute_sql("SELECT a") == [{"a": 1}]

    def test_empty(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(data=None)
        with patch.object(governance, "WAREHOUSE_ID", "wh"), \
             patch.object(governance, "_get_client", return_value=client):
            assert governance._execute_sql("SELECT 1") == []

    def test_failed_raises(self):
        client = MagicMock()
        client.statement_execution.execute_statement.return_value = _resp(
            state=StatementState.FAILED, error=None
        )
        with patch.object(governance, "WAREHOUSE_ID", "wh"), \
             patch.object(governance, "_get_client", return_value=client):
            with pytest.raises(RuntimeError):
                governance._execute_sql("SELECT 1")


class TestEnsureConfigTable:
    def test_calls_create(self):
        with patch.object(governance, "_execute_sql") as m:
            governance._ensure_governance_config_table()
        assert "CREATE TABLE IF NOT EXISTS" in m.call_args[0][0]

    def test_swallows_exception(self):
        with patch.object(governance, "_execute_sql", side_effect=RuntimeError):
            governance._ensure_governance_config_table()  # must not raise


class TestGetTableGovernance:
    def _sql_router(self, meta=None, tags=None, cols=None, rules=None,
                    meta_exc=False, tags_exc=False, cols_exc=False, rules_exc=False):
        """Return a side_effect that dispatches by SQL substring."""
        def router(sql):
            if "information_schema.tables" in sql:
                if meta_exc:
                    raise RuntimeError("meta")
                return meta or []
            if "column_tags" in sql:
                if tags_exc:
                    raise RuntimeError("tags")
                return tags or []
            if "information_schema.columns" in sql:
                if cols_exc:
                    raise RuntimeError("cols")
                return cols or []
            if "CREATE TABLE" in sql:
                return []
            if governance.GOV_CONFIG_TABLE in sql:
                if rules_exc:
                    raise RuntimeError("rules")
                return rules or []
            return []
        return router

    def test_heuristic_pii_classification(self):
        meta = [{"table_owner": "o", "table_type": "MANAGED", "created": "2020",
                 "created_by": "cb", "last_altered": "2021", "last_altered_by": "lb",
                 "comment": "c"}]
        cols = [
            {"column_name": "email", "data_type": "string", "is_nullable": "YES", "comment": None},
            {"column_name": "card_number", "data_type": "string", "is_nullable": "YES", "comment": None},
            {"column_name": "id", "data_type": "bigint", "is_nullable": "NO", "comment": None},
        ]
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(meta=meta, cols=cols)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["owner"] == "o"
        by_name = {c["name"]: c for c in out["columns"]}
        assert by_name["email"]["sensitivity"] == "PII"
        assert by_name["email"]["sensitivity_source"] == "heuristic"
        assert by_name["card_number"]["sensitivity"] == "PCI"
        assert by_name["id"]["sensitivity"] is None
        assert len(out["sensitive_columns"]) == 2

    def test_config_rule_column_pattern_precedence(self):
        cols = [{"column_name": "custref", "data_type": "string", "is_nullable": "YES", "comment": None}]
        rules = [{"column_pattern": "custref", "tag_name": None, "sensitivity": "CONFIDENTIAL"}]
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(cols=cols, rules=rules)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["config_rules_applied"] == 1
        assert out["columns"][0]["sensitivity"] == "CONFIDENTIAL"
        assert out["columns"][0]["sensitivity_source"] == "config_rule"

    def test_config_rule_default_sensitivity_when_missing(self):
        cols = [{"column_name": "foo", "data_type": "string", "is_nullable": "YES", "comment": None}]
        rules = [{"column_pattern": "foo", "tag_name": None}]  # no sensitivity key
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(cols=cols, rules=rules)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["columns"][0]["sensitivity"] == "SENSITIVE"

    def test_tag_rule_branch(self):
        cols = [{"column_name": "plain", "data_type": "string", "is_nullable": "YES", "comment": None}]
        tags = [{"tag_name": "pii_tag", "tag_value": "high"}]
        rules = [{"column_pattern": None, "tag_name": "pii_tag", "sensitivity": "TAGGED"}]
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(tags=tags, cols=cols, rules=rules)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["tags"][0]["name"] == "pii_tag"
        assert out["columns"][0]["sensitivity"] == "TAGGED"
        assert out["columns"][0]["sensitivity_source"] == "tag_rule"

    def test_metadata_and_tags_exceptions_are_soft(self):
        cols = [{"column_name": "id", "data_type": "bigint", "is_nullable": "NO", "comment": None}]
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(cols=cols, meta_exc=True, tags_exc=True)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["owner"] is None
        assert out["tags"] == []
        assert out["columns"][0]["name"] == "id"

    def test_columns_exception_returns_early(self):
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(cols_exc=True)):
            out = governance.get_table_governance("c", "s", "t")
        assert out["columns"] == []
        assert out["sensitive_columns"] == []

    def test_config_rules_exception_soft(self):
        cols = [{"column_name": "email", "data_type": "string", "is_nullable": "YES", "comment": None}]
        with patch.object(governance, "_execute_sql",
                          side_effect=self._sql_router(cols=cols, rules_exc=True)):
            out = governance.get_table_governance("c", "s", "t")
        # config rules failed but heuristic still fires
        assert out["config_rules_applied"] == 0
        assert out["columns"][0]["sensitivity"] == "PII"


class TestRuleCrud:
    def test_list_rules_happy(self):
        with patch.object(governance, "_ensure_governance_config_table"), \
             patch.object(governance, "_execute_sql", return_value=[{"rule_id": "r1"}]):
            assert governance.list_governance_rules() == [{"rule_id": "r1"}]

    def test_list_rules_exception(self):
        with patch.object(governance, "_ensure_governance_config_table"), \
             patch.object(governance, "_execute_sql", side_effect=RuntimeError):
            assert governance.list_governance_rules() == []

    def test_upsert_with_explicit_id(self):
        with patch.object(governance, "_ensure_governance_config_table"), \
             patch.object(governance, "_execute_sql") as m:
            out = governance.upsert_governance_rule(
                {"rule_id": "myrule", "catalog": "c", "schema": "s",
                 "table_pattern": "t%", "column_pattern": "email",
                 "sensitivity": "PII", "notes": "n"}, actor="me")
        assert out == {"rule_id": "myrule", "status": "upserted"}
        assert "INSERT OVERWRITE" in m.call_args[0][0]

    def test_upsert_generates_id(self):
        with patch.object(governance, "_ensure_governance_config_table"), \
             patch.object(governance, "_execute_sql"):
            out = governance.upsert_governance_rule({"catalog": "c"}, actor="me")
        assert out["rule_id"].startswith("rule_")
        assert out["status"] == "upserted"

    def test_delete_rule(self):
        with patch.object(governance, "_ensure_governance_config_table"), \
             patch.object(governance, "_execute_sql") as m:
            out = governance.delete_governance_rule("r1'; DROP")
        assert out["rule_id"] == "r1; DROP"  # quote stripped
        assert out["status"] == "deleted"
        assert "DELETE FROM" in m.call_args[0][0]


class TestDownstreamPropagation:
    def test_no_sensitive_columns_returns_empty(self):
        with patch.object(governance, "get_table_governance",
                          return_value={"sensitive_columns": []}):
            assert governance.get_downstream_sensitivity_propagation("c", "s", "t") == []

    def test_propagation_rows_mapped(self):
        gov = {"sensitive_columns": [{"column": "email", "sensitivity": "PII"}]}
        rows = [{"source_column_name": "email", "target_table_full_name": "c.s.dn",
                 "target_column_name": "email_norm"}]
        with patch.object(governance, "get_table_governance", return_value=gov), \
             patch.object(governance, "_execute_sql", return_value=rows):
            out = governance.get_downstream_sensitivity_propagation("c", "s", "t")
        assert out[0]["target_table"] == "c.s.dn"
        assert out[0]["sensitivity"] == "PII"

    def test_unknown_source_column_defaults_sensitive(self):
        gov = {"sensitive_columns": [{"column": "email", "sensitivity": "PII"}]}
        rows = [{"source_column_name": "other", "target_table_full_name": "t2",
                 "target_column_name": "c2"}]
        with patch.object(governance, "get_table_governance", return_value=gov), \
             patch.object(governance, "_execute_sql", return_value=rows):
            out = governance.get_downstream_sensitivity_propagation("c", "s", "t")
        assert out[0]["sensitivity"] == "SENSITIVE"

    def test_exception_returns_empty(self):
        gov = {"sensitive_columns": [{"column": "email", "sensitivity": "PII"}]}
        with patch.object(governance, "get_table_governance", return_value=gov), \
             patch.object(governance, "_execute_sql", side_effect=RuntimeError):
            assert governance.get_downstream_sensitivity_propagation("c", "s", "t") == []
