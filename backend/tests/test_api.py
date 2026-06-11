"""
API layer — hit the FastAPI app via TestClient with `_execute_sql` mocked.
Verifies request/response shapes and input validation, not backend logic.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(mock_execute_sql, monkeypatch):
    # Register enough responses for the endpoints we test
    mock_execute_sql.register("SHOW CATALOGS", [{"catalog": "main"}])
    mock_execute_sql.register("SHOW SCHEMAS", [{"databaseName": "my_schema"}])
    mock_execute_sql.register(
        "information_schema.tables",
        [
            {"table_name": "t1", "table_type": "TABLE", "table_owner": "o",
             "comment": None, "created": None, "last_altered": None,
             "table_schema": "my_schema"},
        ],
    )
    mock_execute_sql.register("information_schema.columns", [])
    mock_execute_sql.register("system.access.table_lineage", [])
    mock_execute_sql.register("system.access.column_lineage", [])
    mock_execute_sql.register("system.billing.list_prices", [])

    from backend.main import app
    return TestClient(app)


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "version": "1.2.0"}


def test_list_catalogs_endpoint(client):
    r = client.get("/api/catalogs")
    assert r.status_code == 200
    assert r.json() == {"catalogs": ["main"]}


def test_list_schemas_endpoint(client):
    r = client.get("/api/schemas?catalog=main")
    assert r.status_code == 200
    assert r.json() == {"schemas": ["my_schema"]}


def test_lineage_endpoint_returns_expected_shape(client):
    r = client.get("/api/lineage?catalog=main&schema=my_schema")
    assert r.status_code == 200
    body = r.json()
    assert "nodes" in body and "edges" in body
    assert isinstance(body["nodes"], list)
    assert "cached" in body


def test_lineage_cached_flag_flips_on_second_call(client):
    r1 = client.get("/api/lineage?catalog=main&schema=my_schema")
    r2 = client.get("/api/lineage?catalog=main&schema=my_schema")
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True


def test_invalid_catalog_rejected(client):
    # Semicolons / quotes should be blocked by the validator
    r = client.get("/api/lineage?catalog=main;DROP&schema=my_schema")
    assert r.status_code == 400


def test_invalid_schema_rejected(client):
    r = client.get("/api/lineage?catalog=main&schema=my_schema'--")
    assert r.status_code == 400
