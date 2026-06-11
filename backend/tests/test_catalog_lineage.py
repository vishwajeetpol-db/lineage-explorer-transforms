"""
Catalog- and schema-scoped lineage graph building (get_table_lineage).

These guard the catalog-wide refactor:
  - schema=None spans every schema in the catalog
  - columns are keyed by (schema, table_name) so same-named tables in
    different schemas don't clobber each other's column lists
  - the LINEAGE_MAX_NODES safety cap fires for oversized catalogs

`_execute_sql` is monkey-patched (see conftest); no real warehouse is touched.
"""
import pytest
from backend import lineage_service


_TABLES = [
    {"table_schema": "bronze", "table_name": "customers", "table_type": "MANAGED",
     "table_owner": "u", "comment": None, "created": None, "last_altered": None},
    {"table_schema": "bronze", "table_name": "raw_orders", "table_type": "MANAGED",
     "table_owner": "u", "comment": None, "created": None, "last_altered": None},
    {"table_schema": "silver", "table_name": "customers", "table_type": "MANAGED",
     "table_owner": "u", "comment": None, "created": None, "last_altered": None},
]
_COLUMNS = [
    {"table_schema": "bronze", "table_name": "customers", "column_name": "b_id",
     "data_type": "INT", "is_nullable": "NO", "ordinal_position": 1},
    {"table_schema": "silver", "table_name": "customers", "column_name": "s_email",
     "data_type": "STRING", "is_nullable": "YES", "ordinal_position": 1},
    {"table_schema": "bronze", "table_name": "raw_orders", "column_name": "oid",
     "data_type": "INT", "is_nullable": "NO", "ordinal_position": 1},
]
_LINEAGE = [
    {"source_table_full_name": "main.bronze.raw_orders",
     "target_table_full_name": "main.silver.customers",
     "source_type": "TABLE", "target_type": "TABLE",
     "source_path": None, "target_path": None,
     "entity_type": None, "entity_id": None,
     "event_time": "2026-06-01", "created_by": "u"},
]


@pytest.fixture
def lineage_sql(mock_execute_sql):
    """Register canned rows for tables/columns/lineage queries."""
    mock_execute_sql.register("information_schema.tables", _TABLES)
    mock_execute_sql.register("information_schema.columns", _COLUMNS)
    mock_execute_sql.register("system.access.table_lineage", _LINEAGE)
    return mock_execute_sql


def test_catalog_scope_spans_all_schemas(lineage_sql):
    result = lineage_service.get_table_lineage("main", None)
    ids = {n.id for n in result.nodes}
    assert "main.bronze.customers" in ids
    assert "main.bronze.raw_orders" in ids
    assert "main.silver.customers" in ids
    # Cross-schema edge is internal in catalog scope
    assert any(
        e.source == "main.bronze.raw_orders" and e.target == "main.silver.customers"
        for e in result.edges
    )


def test_columns_keyed_by_schema_no_collision(lineage_sql):
    """bronze.customers and silver.customers share a name — each must keep its
    own columns."""
    result = lineage_service.get_table_lineage("main", None)
    by_id = {n.id: n for n in result.nodes}
    assert [c["name"] for c in by_id["main.bronze.customers"].columns] == ["b_id"]
    assert [c["name"] for c in by_id["main.silver.customers"].columns] == ["s_email"]
    # Per-table column cache is also schema-scoped
    assert lineage_service._cache_get("columns:main.bronze.customers")[0]["name"] == "b_id"
    assert lineage_service._cache_get("columns:main.silver.customers")[0]["name"] == "s_email"


def test_schema_scope_keeps_cross_schema_target_external(lineage_sql):
    """In bronze scope, silver.customers is out of scope and appears as an
    external stub node, but the edge to it is preserved."""
    result = lineage_service.get_table_lineage("main", "bronze")
    ids = {n.id for n in result.nodes}
    assert "main.bronze.raw_orders" in ids
    assert any(e.target == "main.silver.customers" for e in result.edges)


def test_catalog_cap_enforced(lineage_sql, monkeypatch):
    monkeypatch.setattr(lineage_service, "LINEAGE_MAX_NODES", 2)
    with pytest.raises(RuntimeError, match="exceeding the"):
        lineage_service.get_table_lineage("main", None)


def test_schema_scope_not_capped(lineage_sql, monkeypatch):
    """The cap only applies to catalog-wide requests."""
    monkeypatch.setattr(lineage_service, "LINEAGE_MAX_NODES", 1)
    # bronze has 2 tables but schema scope must not raise
    result = lineage_service.get_table_lineage("main", "bronze")
    assert any(n.id == "main.bronze.raw_orders" for n in result.nodes)
