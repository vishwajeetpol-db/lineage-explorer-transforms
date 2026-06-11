"""
list_catalogs / list_schemas / list_all_tables use the single-flight cache
helper. Tests verify (a) they return correct shapes and (b) they don't cache
empty results (important — empty cache would block retries after a transient
error).
"""
from backend import lineage_service


def test_list_catalogs_filters_system_catalogs(mock_execute_sql):
    mock_execute_sql.register(
        "SHOW CATALOGS",
        [
            {"catalog": "main"},
            {"catalog": "ws_us_e2_vish_aws_catalog"},
            {"catalog": "system"},                   # must be filtered
            {"catalog": "__databricks_internal"},    # must be filtered
        ],
    )
    result = lineage_service.list_catalogs()
    assert "system" not in result
    assert "__databricks_internal" not in result
    assert "main" in result
    assert "ws_us_e2_vish_aws_catalog" in result


def test_list_catalogs_empty_not_cached(mock_execute_sql):
    """If the query returns nothing, we must NOT cache the empty list — next
    call should retry the query."""
    mock_execute_sql.register("SHOW CATALOGS", [])
    r1 = lineage_service.list_catalogs()
    assert r1 == []
    r2 = lineage_service.list_catalogs()
    assert r2 == []
    # Both calls should have hit DBSQL (cache was skipped on empty)
    catalog_calls = [c for c in mock_execute_sql.calls if "SHOW CATALOGS" in c]
    assert len(catalog_calls) == 2, "empty list was incorrectly cached"


def test_list_catalogs_cached_when_non_empty(mock_execute_sql):
    mock_execute_sql.register("SHOW CATALOGS", [{"catalog": "main"}])
    lineage_service.list_catalogs()
    lineage_service.list_catalogs()
    lineage_service.list_catalogs()
    catalog_calls = [c for c in mock_execute_sql.calls if "SHOW CATALOGS" in c]
    assert len(catalog_calls) == 1, "result was not cached after first call"


def test_list_schemas_filters_defaults(mock_execute_sql):
    mock_execute_sql.register(
        "SHOW SCHEMAS",
        [
            {"databaseName": "my_schema"},
            {"databaseName": "information_schema"},
            {"databaseName": "default"},
        ],
    )
    result = lineage_service.list_schemas("main")
    assert "information_schema" not in result
    assert "default" not in result
    assert "my_schema" in result
