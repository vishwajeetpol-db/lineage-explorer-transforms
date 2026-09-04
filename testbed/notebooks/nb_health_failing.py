# Databricks notebook source
# T2-15b  Always fails → run-health "Failing" verdict + root-cause tracing.
# Reads a table that does not exist, so the failure is a realistic upstream
# break rather than a bare raise.
# Expected: health popover reads Failing at 0% success; the Root Cause panel
# surfaces this producer for its downstream table.
try:
    CATALOG = dbutils.widgets.get("CATALOG")
except Exception:
    CATALOG = "__CATALOG__"
try:
    SCHEMA = dbutils.widgets.get("SCHEMA")
except Exception:
    SCHEMA = "__SCHEMA__"

print("Intentional test-bed failure: selecting from a deliberately missing table.")
spark.sql(f"SELECT * FROM {CATALOG}.{SCHEMA}.table_that_does_not_exist_by_design").count()
