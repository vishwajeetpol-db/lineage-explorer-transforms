-- =============================================================================
-- T1-2  sql_task source file.
-- Run by the job `bt-sqltask-order-agg` as a SQL FILE task — the producer type
-- the resolver has a branch for but that was never cleanly exercised.
-- Expected: transformation lineage for sqltask_order_agg resolves from THIS
-- file, with channel_revenue = round(sum(...), 2) [AGGREGATION] and
-- channel_code = concat('CH-', channel) [STRING_FN].
-- =============================================================================
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.sqltask_order_agg AS
WITH paid AS (
  SELECT
    o.order_id,
    o.channel,
    o.order_date,
    oi.quantity,
    oi.unit_price,
    oi.discount_pct
  FROM __CATALOG__.silver.fct_orders o
  JOIN __CATALOG__.silver.fct_order_items oi ON oi.order_id = o.order_id
  WHERE o.status <> 'cancelled'
)
SELECT
  CONCAT('CH-', paid.channel)                                              AS channel_code,
  CAST(paid.order_date AS DATE)                                            AS order_date,
  ROUND(SUM(paid.quantity * paid.unit_price * (1 - paid.discount_pct)), 2) AS channel_revenue,
  COUNT(DISTINCT paid.order_id)                                            AS order_count,
  ROUND(AVG(paid.unit_price), 2)                                           AS avg_unit_price
FROM paid
GROUP BY CONCAT('CH-', paid.channel), CAST(paid.order_date AS DATE);
