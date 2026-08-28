-- =============================================================================
-- T1-4  SQL-defined DLT (Lakeflow Declarative Pipeline) source.
--
-- The only DLT fixture in this workspace today is Python-defined
-- (lineage_lab.dlt_order_enriched), so the SQL-DLT path is untested. This
-- declares BOTH a streaming table and a materialized view so the definition-
-- based resolver and the PIPELINE resolver are both exercised.
--
-- Expected transformation lineage:
--   sqldlt_orders_clean.status_norm   = upper(status)                [STRING_FN]
--   sqldlt_orders_clean.channel_code  = concat('CH-', channel)       [STRING_FN]
--   sqldlt_channel_revenue.revenue    = round(sum(net_amount), 2)    [AGGREGATION]
-- Streams from __SCHEMA__.stream_src_orders (append-only) rather than
-- silver.fct_orders, which the medallion job overwrites — a streaming read of
-- an overwritten table fails on the next run.
-- =============================================================================

CREATE OR REFRESH STREAMING TABLE sqldlt_orders_clean
  COMMENT 'Cleaned order stream: normalised status and prefixed channel code.'
AS SELECT
  order_id,
  customer_id,
  CAST(order_date AS DATE)      AS order_date,
  UPPER(status)                 AS status_norm,
  CONCAT('CH-', channel)        AS channel_code
FROM STREAM(__CATALOG__.__SCHEMA__.stream_src_orders);

CREATE OR REFRESH MATERIALIZED VIEW sqldlt_channel_revenue
  COMMENT 'Revenue per channel per day, aggregated from the cleaned order stream.'
AS SELECT
  c.channel_code,
  c.order_date,
  ROUND(SUM(oi.net_amount), 2)              AS revenue,
  COUNT(DISTINCT c.order_id)                AS order_count,
  ROUND(AVG(oi.net_amount), 2)              AS avg_order_value
FROM LIVE.sqldlt_orders_clean c
JOIN __CATALOG__.silver.fct_order_items oi ON oi.order_id = c.order_id
GROUP BY c.channel_code, c.order_date;
