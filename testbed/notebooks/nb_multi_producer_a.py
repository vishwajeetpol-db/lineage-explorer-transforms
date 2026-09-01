# Databricks notebook source
# T1-6a  Multi-producer divergence — PRODUCER A ("correct" logic).
#
# Both this notebook and nb_multi_producer_b write __SCHEMA__.multi_metrics.
# A applies the discount; B does not. That is a silent consistency hazard: the
# table's net_revenue means different things depending on which job ran last.
#
# Expected: the Column Transformation panel's "Producers" tab shows a
# side-by-side matrix for multi_metrics and FLAGS net_revenue as divergent
# (A: qty*price*(1-discount)  vs  B: qty*price).

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
  o.customer_id                                                            AS customer_id,
  CAST(o.order_date AS DATE)                                               AS order_date,
  ROUND(SUM(oi.quantity * oi.unit_price * (1 - oi.discount_pct)), 2)       AS net_revenue,
  COUNT(DISTINCT o.order_id)                                               AS order_count,
  ROUND(AVG(oi.unit_price), 2)                                             AS avg_unit_price,
  'producer_a'                                                             AS written_by
FROM {CATALOG}.silver.fct_orders o
JOIN {CATALOG}.silver.fct_order_items oi ON oi.order_id = o.order_id
GROUP BY o.customer_id, CAST(o.order_date AS DATE)
""")
print("producer A wrote multi_metrics (discount applied)")
