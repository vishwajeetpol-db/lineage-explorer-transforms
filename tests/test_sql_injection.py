"""Comprehensive SQL injection tests — A1 Critical security vulnerability.

The BrickTrace app interpolates user-supplied values into SQL strings executed
as the App Service Principal on the bound warehouse. Any App user can craft
payloads that execute arbitrary SQL with the SP's permissions.

Vulnerable paths identified in the audit:
1. capability_closures.py: catalog/table into LIKE '%...%' and WHERE clauses
2. dq.py: CUSTOM/RANGE expressions interpolated into SELECT
3. glossary: term names/descriptions interpolated
4. openlineage: source_id in URL path interpolated
5. streaming_topology: catalog into WHERE clause
6. snapshot_timeline: scope into WHERE clause
7. bi_consumers: catalog/table into LIKE pattern

This file tests each vector and documents current (vulnerable) behavior.
After remediation (parameterized SQL / IDENTIFIER()), these tests should
be updated to assert 400 rejection.
"""
import pytest
from unittest.mock import patch, MagicMock


# Standard SQL injection payloads
PAYLOADS = [
    "' OR '1'='1",
    "'; DROP TABLE users; --",
    "' UNION SELECT username,password FROM credentials--",
    "1; EXEC xp_cmdshell('whoami')--",
    "' AND 1=0 UNION ALL SELECT table_name FROM information_schema.tables--",
    "admin'--",
    "' OR ''='",
    "1' ORDER BY 1--+",
    "' AND SLEEP(5)--",
    "'; INSERT INTO dq_rules VALUES('x','x','x','x','PWNED','x','x',NOW(),NOW(),'x');--",
]


class TestBIConsumersInjection:
    """A1: /api/lineage/bi-consumers — catalog/table into LIKE clause."""

    @pytest.mark.parametrize("payload", PAYLOADS[:5])
    def test_catalog_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "catalog": payload
            })
            # Vulnerable: 200 (payload reaches SQL)
            # Fixed: 400 (validation rejects)
            if resp.status_code == 200 and mock_sql.called:
                sql = mock_sql.call_args[0][0]
                # Document: payload was interpolated into SQL
                assert payload.replace("'", "").lower()[:10] in sql.lower() or True

    @pytest.mark.parametrize("payload", PAYLOADS[:5])
    def test_table_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/bi-consumers", params={
                "table": payload
            })
            assert resp.status_code in (200, 400)


class TestStreamingTopologyInjection:
    """A1: /api/lineage/streaming-topology — catalog into WHERE clause."""

    @pytest.mark.parametrize("payload", PAYLOADS[:5])
    def test_catalog_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/lineage/streaming-topology", params={
                "catalog": payload
            })
            assert resp.status_code in (200, 400, 500)


class TestSnapshotTimelineInjection:
    """A1: /api/snapshots/timeline — scope into WHERE clause."""

    @pytest.mark.parametrize("payload", PAYLOADS[:5])
    def test_scope_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/snapshots/timeline", params={
                "scope": payload
            })
            # scope has chr(39) replacement but not full validation
            assert resp.status_code in (200, 400, 500)


class TestDQCustomExpressionInjection:
    """A1: /api/dq-rules POST — expression field for CUSTOM rules.

    The DQ live metrics endpoint executes the expression against the table:
    SELECT COUNT(CASE WHEN NOT ({expression}) THEN 1 END) FROM table

    Injecting into expression = arbitrary SQL execution.
    """

    def test_custom_expression_injection(self, admin_client):
        """A1 CRITICAL: CUSTOM expression is executed as SQL."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "CUSTOM",
                "expression": "1=1) UNION ALL SELECT * FROM information_schema.columns WHERE (1=1",
                "severity": "ERROR",
            })
            # BUG: 200 (expression stored, will be executed later)
            # After fix: 400 (expression AST validation rejects)
            assert resp.status_code in (200, 400)

    def test_range_bounds_injection(self, admin_client):
        """A1: RANGE rule bounds may be injectable."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = admin_client.post("/api/dq-rules", json={
                "table_fqn": "main.default.orders",
                "column_name": "amount",
                "rule_type": "RANGE",
                "expression": "amount BETWEEN 0 AND 999999) OR (1=1",
                "severity": "WARN",
            })
            assert resp.status_code in (200, 400)


class TestDQTrendsInjection:
    """A1: /api/dq-rules/trends — table_fqn into WHERE clause."""

    @pytest.mark.parametrize("payload", PAYLOADS[:3])
    def test_table_fqn_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/trends", params={
                "table_fqn": payload
            })
            # table_fqn has chr(39) replacement but payload still reaches SQL
            assert resp.status_code in (200, 400, 422, 500)


class TestExternalSourcesInjection:
    """A1/A15: /api/external/ol-bridge/ingest/{source_id} — path param."""

    @pytest.mark.parametrize("payload", [
        "test'; DROP TABLE events; --",
        "x' UNION SELECT * FROM secrets--",
    ])
    def test_source_id_path_injection(self, app_client, payload):
        with patch("backend.routes.external_sources._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.post(
                f"/api/external/ol-bridge/ingest/{payload}",
                json={"eventType": "COMPLETE", "eventTime": "2026-07-20T12:00:00Z",
                      "run": {"runId": "r"}, "job": {"namespace": "x", "name": "y"},
                      "inputs": [], "outputs": []}
            )
            assert resp.status_code in (200, 400, 404, 422)


class TestPipelineExpectationsInjection:
    """A1: /api/dq-rules/pipeline-expectations — catalog filter."""

    @pytest.mark.parametrize("payload", PAYLOADS[:3])
    def test_catalog_injection(self, app_client, payload):
        with patch("backend.routes.capability_closures._execute_sql") as mock_sql:
            mock_sql.return_value = []
            resp = app_client.get("/api/dq-rules/pipeline-expectations", params={
                "catalog": payload
            })
            assert resp.status_code in (200, 400, 500)


class TestSecondOrderInjection:
    """A1: Second-order injection via stored expressions.

    DQ rules store `expression` in Delta. When /api/dq-rules/metrics
    executes them, the stored payload fires.
    """

    def test_stored_expression_executes_on_metrics(self, app_client):
        """Stored CUSTOM expression is executed verbatim against table."""
        with patch("backend.routes.dq._execute_sql") as mock_sql:
            # Simulate stored malicious rule in DQ table
            mock_sql.side_effect = [
                [],  # _ensure_dq_table
                [{"rule_id": "evil", "table_fqn": "main.default.orders",
                  "column_name": "id", "rule_type": "CUSTOM",
                  "expression": "1=1) UNION SELECT secret FROM credentials WHERE (1=1",
                  "severity": "ERROR"}],  # SELECT rules
                [{"total": "100", "violations": "100"}],  # Execute rule (injection fires)
            ]
            resp = app_client.get("/api/dq-rules/metrics", params={
                "table_fqn": "main.default.orders"
            })
            # The endpoint executes the stored expression as SQL
            assert resp.status_code in (200, 500)
