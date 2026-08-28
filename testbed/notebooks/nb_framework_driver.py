# Databricks notebook source
# T1-7  Metadata-driven ETL framework → deep framework analysis fixture.
#
# This notebook contains NO column logic. Every expression lives in the
# __SCHEMA__.etl_config table and is rendered into SQL at runtime, which is
# exactly the shape that defeats direct source parsing.
#
# Expected: normal transformation analysis finds no column logic for
# framework_output, and the panel's "deep framework analysis" pass detects the
# config mechanism, reads etl_config, and derives the 7 column rules from it.

# Widget override if the job passes one; otherwise the value substituted at
# upload time by build_testbed.sh. Same try/except idiom as notebooks/run_pipeline.py.
try:
    CATALOG = dbutils.widgets.get("CATALOG")
except Exception:
    CATALOG = "__CATALOG__"
try:
    SCHEMA = dbutils.widgets.get("SCHEMA")
except Exception:
    SCHEMA = "__SCHEMA__"
TARGET = "framework_output"

# COMMAND ----------

rules = spark.sql(f"""
    SELECT target_column, source_expr
    FROM {CATALOG}.{SCHEMA}.etl_config
    WHERE target_table = '{TARGET}' AND active
""").collect()

if not rules:
    raise ValueError(f"no active etl_config rules for {TARGET}")

projection = ",\n  ".join(f"{r['source_expr']} AS {r['target_column']}" for r in rules)
sql = f"""
CREATE OR REPLACE TABLE {CATALOG}.{SCHEMA}.{TARGET} AS
SELECT
  {projection}
FROM {CATALOG}.{SCHEMA}.v_order_summary
"""
print(sql)
spark.sql(sql)
print(f"config-driven build wrote {TARGET} from {len(rules)} rules")
