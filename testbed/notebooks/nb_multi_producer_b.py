# Databricks notebook source
# T1-6b  Multi-producer divergence — PRODUCER B (drifted logic).
#
# Same target table and same column names as nb_multi_producer_a, but
# net_revenue IGNORES discount_pct and avg_unit_price is a weighted average.
# This is the divergence the compare-producers matrix exists to catch.

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

# COMMAND ----------

spark.sql(f"""
CREATE OR REPLACE TABLE {CATALOG}.{SCHEMA}.multi_metrics AS
SELECT
  o.customer_id                                                       AS customer_id,
  CAST(o.order_date AS DATE)                                          AS order_date,
  ROUND(SUM(oi.quantity * oi.unit_price), 2)                          AS net_revenue,
  COUNT(o.order_id)                                                   AS order_count,
  ROUND(SUM(oi.unit_price * oi.quantity) / NULLIF(SUM(oi.quantity), 0), 2) AS avg_unit_price,
  'producer_b'                                                        AS written_by
FROM {CATALOG}.silver.fct_orders o
JOIN {CATALOG}.silver.fct_order_items oi ON oi.order_id = o.order_id
GROUP BY o.customer_id, CAST(o.order_date AS DATE)
""")
print("producer B wrote multi_metrics (discount IGNORED — deliberate drift)")
