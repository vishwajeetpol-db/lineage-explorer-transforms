"""Coverage-focused tests for backend/routes/glossary.py.

Drives the term/domain/kpi upsert+delete paths, for-table, link,
propagate-suggestions, and lineage-overlay by mocking the module-level
_execute_sql. No backend/ edits; everything mocked; fast + offline.
"""
from unittest.mock import patch

import pytest


@pytest.fixture
def mock_sql():
    with patch("backend.routes.glossary._execute_sql") as m:
        m.return_value = []
        yield m


# A canonical UUID for the id-shaped fields (term_id/domain_id/kpi_id).
FIXED_UUID = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


def _all_sql(mock_sql) -> str:
    """Every statement the handler pushed to the warehouse, joined.

    Asserting over all calls (rather than call_args) keeps these tests immune to
    the lazy CREATE TABLE IF NOT EXISTS statements _lazy_ensure() may emit first.
    """
    return "\n".join(c.args[0] for c in mock_sql.call_args_list)


class TestExecuteSqlAndEnsure:
    def test_execute_sql_no_warehouse(self):
        import backend.routes.glossary as g
        with patch.object(g, "WAREHOUSE_ID", ""):
            with pytest.raises(RuntimeError, match="No SQL warehouse"):
                g._execute_sql("SELECT 1")

    def test_execute_sql_success_rows(self):
        import backend.routes.glossary as g
        from unittest.mock import MagicMock
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result.data_array = [["v1", "v2"]]
        col_a, col_b = MagicMock(), MagicMock()
        col_a.name, col_b.name = "a", "b"
        resp.manifest.schema.columns = [col_a, col_b]
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            out = g._execute_sql("SELECT a, b")
        assert out == [{"a": "v1", "b": "v2"}]

    def test_execute_sql_empty_result(self):
        import backend.routes.glossary as g
        from unittest.mock import MagicMock
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.SUCCEEDED
        resp.result = None
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            assert g._execute_sql("SELECT 1") == []

    def test_execute_sql_failed_state(self):
        import backend.routes.glossary as g
        from unittest.mock import MagicMock
        from databricks.sdk.service.sql import StatementState

        client = MagicMock()
        resp = MagicMock()
        resp.status.state = StatementState.FAILED
        resp.status.error.message = "bad"
        client.statement_execution.execute_statement.return_value = resp
        with patch.object(g, "WAREHOUSE_ID", "wh"), patch.object(
            g, "_get_client", return_value=client
        ):
            with pytest.raises(RuntimeError, match="SQL failed"):
                g._execute_sql("SELECT 1")

    def test_ensure_tables_swallows_error(self):
        import backend.routes.glossary as g
        with patch.object(g, "_execute_sql", side_effect=RuntimeError("nope")):
            g._ensure_tables()  # should not raise


class TestTerms:
    def test_list_terms_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"term_id": "t1", "name": "Revenue"}]
        resp = app_client.get("/api/glossary/terms")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    def test_list_terms_with_filters(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/glossary/terms",
            params={"q": "rev'x", "domain": "finance", "status": "approved", "limit": 10},
        )
        assert resp.status_code == 200

    def test_list_terms_sql_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/glossary/terms")
        assert resp.status_code == 500

    def test_get_term_found(self, app_client, mock_sql):
        mock_sql.side_effect = [[{"term_id": "t1", "name": "Rev"}], [{"link_id": "l1"}]]
        resp = app_client.get("/api/glossary/terms/t1")
        assert resp.status_code == 200
        assert resp.json()["term"]["term_id"] == "t1"

    def test_get_term_not_found(self, app_client, mock_sql):
        mock_sql.return_value = []
        resp = app_client.get("/api/glossary/terms/missing")
        assert resp.status_code == 404

    def test_get_term_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/glossary/terms/t1")
        assert resp.status_code == 500

    def test_upsert_term_ok(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/terms",
            json={"name": "Rev", "definition": "money's in", "domain": "fin"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_upsert_term_with_id(self, app_client, mock_sql):
        # A supplied id must be a UUID — see TestSecurity for the non-UUID path.
        resp = app_client.post(
            "/api/glossary/terms",
            json={"term_id": FIXED_UUID, "name": "Rev", "definition": "d"},
        )
        assert resp.status_code == 200
        assert resp.json()["term_id"] == FIXED_UUID

    def test_upsert_term_missing_required_422(self, app_client, mock_sql):
        resp = app_client.post("/api/glossary/terms", json={"name": "only"})
        assert resp.status_code == 422

    def test_upsert_term_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.post(
            "/api/glossary/terms", json={"name": "R", "definition": "d"}
        )
        assert resp.status_code == 500

    # DELETE is admin-gated, so these two drive it as an admin.
    def test_delete_term_ok(self, admin_client, mock_sql):
        resp = admin_client.delete("/api/glossary/terms/t1")
        assert resp.status_code == 200

    def test_delete_term_error_500(self, admin_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = admin_client.delete("/api/glossary/terms/t1")
        assert resp.status_code == 500


class TestDomains:
    def test_list_domains_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"domain_id": "d1"}]
        resp = app_client.get("/api/glossary/domains")
        assert resp.status_code == 200

    def test_list_domains_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/glossary/domains")
        assert resp.status_code == 500

    def test_upsert_domain_ok(self, app_client, mock_sql):
        resp = app_client.post("/api/glossary/domains", json={"name": "Finance"})
        assert resp.status_code == 200
        assert "domain_id" in resp.json()

    def test_upsert_domain_missing_name_422(self, app_client, mock_sql):
        resp = app_client.post("/api/glossary/domains", json={})
        assert resp.status_code == 422

    def test_upsert_domain_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.post("/api/glossary/domains", json={"name": "F"})
        assert resp.status_code == 500


class TestKpis:
    def test_list_kpis_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"kpi_id": "k1"}]
        resp = app_client.get("/api/glossary/kpis")
        assert resp.status_code == 200

    def test_list_kpis_with_domain(self, app_client, mock_sql):
        resp = app_client.get("/api/glossary/kpis", params={"domain": "fin"})
        assert resp.status_code == 200

    def test_list_kpis_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get("/api/glossary/kpis")
        assert resp.status_code == 500

    def test_upsert_kpi_ok(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/kpis",
            json={"name": "MRR", "definition": "monthly", "formula_sql": "sum(x)"},
        )
        assert resp.status_code == 200
        assert "kpi_id" in resp.json()

    def test_upsert_kpi_missing_required_422(self, app_client, mock_sql):
        resp = app_client.post("/api/glossary/kpis", json={"name": "x"})
        assert resp.status_code == 422

    def test_upsert_kpi_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.post(
            "/api/glossary/kpis", json={"name": "M", "definition": "d"}
        )
        assert resp.status_code == 500


class TestLinking:
    def test_for_table_ok(self, app_client, mock_sql):
        mock_sql.return_value = [{"link_id": "l1", "term_name": "Rev"}]
        resp = app_client.get(
            "/api/glossary/for-table",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        assert resp.json()["links"]

    def test_for_table_missing_params_422(self, app_client, mock_sql):
        resp = app_client.get("/api/glossary/for-table", params={"catalog": "c"})
        assert resp.status_code == 422

    def test_for_table_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get(
            "/api/glossary/for-table",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 500

    def test_link_ok(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/link",
            json={"term_id": "t1", "asset_type": "table", "asset_fqn": "c.s.t"},
        )
        assert resp.status_code == 200
        assert "link_id" in resp.json()

    def test_link_missing_required_422(self, app_client, mock_sql):
        resp = app_client.post("/api/glossary/link", json={"term_id": "t1"})
        assert resp.status_code == 422

    def test_link_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.post(
            "/api/glossary/link",
            json={"term_id": "t1", "asset_type": "table", "asset_fqn": "c.s.t"},
        )
        assert resp.status_code == 500


class TestPropagateSuggestions:
    def test_no_source_terms(self, app_client, mock_sql):
        mock_sql.return_value = []  # no source terms
        resp = app_client.get(
            "/api/glossary/propagate-suggestions",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        assert resp.json()["suggestions"] == []

    def test_no_downstream_tables(self, app_client, mock_sql):
        # 1) source terms present, 2) BFS hop returns nothing
        mock_sql.side_effect = [
            [{"term_id": "t1", "term_name": "Rev", "domain": "fin"}],
            [],
        ]
        resp = app_client.get(
            "/api/glossary/propagate-suggestions",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "No downstream tables found"

    def test_with_suggestions(self, app_client, mock_sql):
        # source terms -> BFS hop1 finds a downstream -> hop2 empty ->
        # missing-terms check for the downstream returns no existing links
        mock_sql.side_effect = [
            [{"term_id": "t1", "term_name": "Rev", "domain": "fin"}],
            [{"target_table_full_name": "c.s.gold"}],
            [],  # hop 2
            [],  # hop 3
            [],  # existing links for c.s.gold -> none, so missing
        ]
        resp = app_client.get(
            "/api/glossary/propagate-suggestions",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["downstream_count"] == 1
        assert data["suggestion_count"] == 1

    def test_bfs_hop_exception_swallowed(self, app_client, mock_sql):
        # source terms present, then every downstream query raises inside hop loop
        def _se(sql):
            if "system.access.table_lineage" in sql:
                raise RuntimeError("no browse")
            return [{"term_id": "t1", "term_name": "Rev", "domain": "fin"}]

        mock_sql.side_effect = _se
        resp = app_client.get(
            "/api/glossary/propagate-suggestions",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 200
        assert resp.json()["note"] == "No downstream tables found"

    def test_outer_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get(
            "/api/glossary/propagate-suggestions",
            params={"catalog": "c", "schema": "s", "table": "t"},
        )
        assert resp.status_code == 500


class TestLineageOverlay:
    def test_overlay_with_schema(self, app_client, mock_sql):
        mock_sql.side_effect = [
            [
                {
                    "asset_fqn": "c.s.t",
                    "column_name": "col",
                    "asset_type": "table",
                    "term_id": "t1",
                    "term_name": "Rev",
                    "domain": "fin",
                    "status": "approved",
                    "domain_color": "#fff",
                }
            ],
            [{"kpi_id": "k1", "name": "MRR"}],
        ]
        resp = app_client.get(
            "/api/glossary/lineage-overlay",
            params={"catalog": "c", "schema": "s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["table_count"] == 1
        assert data["overlay"][0]["domains"] == ["fin"]

    def test_overlay_no_schema(self, app_client, mock_sql):
        mock_sql.side_effect = [[], []]
        resp = app_client.get(
            "/api/glossary/lineage-overlay", params={"catalog": "c"}
        )
        assert resp.status_code == 200
        assert resp.json()["table_count"] == 0

    def test_overlay_error_500(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError("boom")
        resp = app_client.get(
            "/api/glossary/lineage-overlay", params={"catalog": "c"}
        )
        assert resp.status_code == 500


class TestSecurity:
    """Regression tests for the SQL-injection and authz findings in this module."""

    # --- FIX 1: client-supplied ids must be UUIDs, and are escaped anyway ---
    def test_upsert_term_rejects_non_uuid_id(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/terms",
            json={"term_id": "not-a-uuid", "name": "R", "definition": "d"},
        )
        assert resp.status_code == 400
        assert "MERGE INTO" not in _all_sql(mock_sql)

    def test_upsert_term_rejects_merge_injection_payload(self, app_client, mock_sql):
        # The confirmed exploit: term_id was interpolated raw into BOTH the MERGE
        # source and the INSERT VALUES clause, so this payload exfiltrated rows
        # through the term_id column on the next GET /terms.
        payload = (
            "x'||CAST((SELECT concat_ws(',', collect_list(salary)) "
            "FROM main.hr.salaries) AS STRING)||'y"
        )
        resp = app_client.post(
            "/api/glossary/terms",
            json={"name": "n", "definition": "d", "term_id": payload},
        )
        assert resp.status_code == 400
        assert "collect_list" not in _all_sql(mock_sql)

    def test_upsert_term_accepts_uuid_id(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/terms",
            json={"term_id": FIXED_UUID, "name": "R", "definition": "d"},
        )
        assert resp.status_code == 200
        assert FIXED_UUID in _all_sql(mock_sql)

    def test_upsert_domain_rejects_non_uuid_id(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/domains", json={"domain_id": "x'||y", "name": "F"}
        )
        assert resp.status_code == 400
        assert "MERGE INTO" not in _all_sql(mock_sql)

    def test_upsert_kpi_rejects_non_uuid_id(self, app_client, mock_sql):
        resp = app_client.post(
            "/api/glossary/kpis",
            json={"kpi_id": "x'||y", "name": "M", "definition": "d"},
        )
        assert resp.status_code == 400
        assert "MERGE INTO" not in _all_sql(mock_sql)

    # --- FIX 2: the backslash bypass on the free-text list filters ---
    # `\'` is an escape sequence in Databricks SQL, so quote-doubling alone turned
    # a leading `\'` into `\''` whose second quote CLOSED the literal.
    def test_list_terms_escapes_backslash_payload_in_q(self, app_client, mock_sql):
        payload = "\\' OR 1=1 --"
        resp = app_client.get("/api/glossary/terms", params={"q": payload})
        assert resp.status_code == 200
        sql = _all_sql(mock_sql)
        assert "\\\\''" in sql              # backslash doubled first, then the quote
        assert payload.lower() not in sql.lower()   # never lands verbatim

    def test_list_terms_escapes_backslash_payload_in_domain(self, app_client, mock_sql):
        payload = "\\' UNION SELECT 1 --"
        resp = app_client.get("/api/glossary/terms", params={"domain": payload})
        assert resp.status_code == 200
        sql = _all_sql(mock_sql)
        assert "\\\\''" in sql
        assert payload not in sql

    def test_list_terms_rejects_unknown_status(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/glossary/terms", params={"status": "' OR 1=1 --"}
        )
        assert resp.status_code == 400
        assert "SELECT * FROM" not in _all_sql(mock_sql)

    def test_list_terms_allows_every_known_status(self, app_client, mock_sql):
        for status in ("draft", "approved", "deprecated"):
            resp = app_client.get("/api/glossary/terms", params={"status": status})
            assert resp.status_code == 200, status

    def test_get_term_escapes_backslash_payload(self, app_client, mock_sql):
        mock_sql.return_value = []
        resp = app_client.get("/api/glossary/terms/" + "\\'--")
        assert resp.status_code == 404          # escaped, so it simply matches nothing
        assert "\\\\''" in _all_sql(mock_sql)

    # --- ALSO: identifier params on the graph/overlay paths are allow-listed ---
    def test_lineage_overlay_rejects_bad_catalog(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/glossary/lineage-overlay", params={"catalog": "c' OR '1'='1"}
        )
        assert resp.status_code == 400

    def test_lineage_overlay_rejects_bad_schema(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/glossary/lineage-overlay", params={"catalog": "c", "schema": "s'--"}
        )
        assert resp.status_code == 400

    def test_for_table_rejects_bad_identifier(self, app_client, mock_sql):
        resp = app_client.get(
            "/api/glossary/for-table",
            params={"catalog": "c", "schema": "s", "table": "t' OR '1'='1"},
        )
        assert resp.status_code == 400

    # --- FIX 3: only the destructive DELETE is gated ---
    def test_delete_term_non_admin_403(self, non_admin_client, mock_sql):
        resp = non_admin_client.delete("/api/glossary/terms/t1")
        assert resp.status_code == 403
        assert "DELETE FROM" not in _all_sql(mock_sql)

    def test_delete_term_anonymous_403(self, app_client, mock_sql):
        resp = app_client.delete("/api/glossary/terms/t1")
        assert resp.status_code == 403
        assert "DELETE FROM" not in _all_sql(mock_sql)

    def test_delete_term_admin_allowed(self, admin_client, mock_sql):
        resp = admin_client.delete("/api/glossary/terms/t1")
        assert resp.status_code == 200
        assert "DELETE FROM" in _all_sql(mock_sql)

    def test_upsert_term_open_to_non_admin_and_records_caller(
        self, non_admin_client, mock_sql
    ):
        resp = non_admin_client.post(
            "/api/glossary/terms", json={"name": "R", "definition": "d"}
        )
        assert resp.status_code == 200
        sql = _all_sql(mock_sql)
        assert "user@test.com" in sql       # real caller, not a hardcoded 'app'
        assert "'app'" not in sql

    def test_upsert_kpi_records_caller(self, non_admin_client, mock_sql):
        resp = non_admin_client.post(
            "/api/glossary/kpis", json={"name": "M", "definition": "d"}
        )
        assert resp.status_code == 200
        sql = _all_sql(mock_sql)
        assert "user@test.com" in sql
        assert "'app'" not in sql

    def test_link_term_records_caller(self, non_admin_client, mock_sql):
        resp = non_admin_client.post(
            "/api/glossary/link",
            json={"term_id": "t1", "asset_type": "table", "asset_fqn": "c.s.t"},
        )
        assert resp.status_code == 200
        sql = _all_sql(mock_sql)
        assert "user@test.com" in sql
        assert "'app'" not in sql

    def test_upsert_domain_owner_defaults_to_caller(self, non_admin_client, mock_sql):
        # glossary_domains has no created_by column, so a blank owner falls back
        # to the caller — otherwise the row would be unattributable.
        resp = non_admin_client.post("/api/glossary/domains", json={"name": "Finance"})
        assert resp.status_code == 200
        assert "user@test.com" in _all_sql(mock_sql)

    def test_upsert_domain_keeps_explicit_owner(self, non_admin_client, mock_sql):
        resp = non_admin_client.post(
            "/api/glossary/domains", json={"name": "Finance", "owner": "cfo@test.com"}
        )
        assert resp.status_code == 200
        assert "cfo@test.com" in _all_sql(mock_sql)

    # --- ALSO: 500s no longer echo the raw SQL error back to the caller ---
    def test_error_detail_does_not_leak_sql(self, app_client, mock_sql):
        mock_sql.side_effect = RuntimeError(
            "SQL failed: [TABLE_OR_VIEW_NOT_FOUND] main.hr.salaries"
        )
        resp = app_client.get("/api/glossary/terms")
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert "SQL failed" not in detail
        assert "salaries" not in detail
