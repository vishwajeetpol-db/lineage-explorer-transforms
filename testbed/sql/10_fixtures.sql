-- =============================================================================
-- BrickTrace test bed — SQL fixtures (Tier 1 + Tier 2)
-- Tokens __CATALOG__ / __SCHEMA__ are substituted by build_testbed.sh.
-- Every statement is idempotent: safe to re-run.
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS __CATALOG__.__SCHEMA__
  COMMENT 'BrickTrace test bed — synthetic fixtures covering every supported producer type and capability panel. Safe to DROP.';

-- ---------------------------------------------------------------------------
-- T1-1  Plain VIEW  → definition-based resolution (SHOW CREATE TABLE path).
-- No VIEW existed anywhere in this metastore before, so this producer type
-- was never exercised. Expected: transformation lineage resolves WITHOUT a
-- discovery/lineage lag, with order_net_amount = round(sum(...), 2).
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW __CATALOG__.__SCHEMA__.v_order_summary
  COMMENT 'One row per order with revenue and margin, derived from silver fact tables.'
AS
SELECT
  o.order_id,
  o.customer_id,
  CAST(o.order_date AS DATE)                              AS order_date,
  UPPER(o.status)                                         AS status_norm,
  ROUND(SUM(oi.net_amount), 2)                            AS order_net_amount,
  ROUND(SUM(oi.net_amount - (oi.quantity * oie.unit_cost)), 2) AS order_margin,
  COUNT(DISTINCT oi.order_item_id)                        AS item_count
FROM __CATALOG__.silver.fct_orders o
JOIN __CATALOG__.silver.fct_order_items oi     ON oi.order_id = o.order_id
JOIN __CATALOG__.silver.order_items_enriched oie ON oie.order_item_id = oi.order_item_id
GROUP BY o.order_id, o.customer_id, CAST(o.order_date AS DATE), UPPER(o.status);

-- ---------------------------------------------------------------------------
-- T1-7  Metadata-driven ETL config table → deep framework analysis.
-- NOTE on quoting: the nested quotes below are backslash-escaped (\'), NOT
-- doubled (''). Databricks parses '..''..' as ADJACENT string literals and
-- concatenates them, silently dropping the quotes — the INSERT succeeds and the
-- stored expression is corrupt (DATE_FORMAT(order_date, yyyy-MM)), which only
-- surfaces later as UNRESOLVED_COLUMN `yyyy` when the config is evaluated.
--
-- nb_framework_driver.py reads THIS table and builds framework_output from it,
-- so no column logic is visible in the notebook source. Expected: normal
-- analysis finds nothing, and "deep framework analysis" detects the config
-- mechanism, reads these rows, and derives the per-column transformations.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS __CATALOG__.__SCHEMA__.etl_config (
  target_table   STRING COMMENT 'Table the rule builds',
  target_column  STRING COMMENT 'Output column name',
  source_expr    STRING COMMENT 'SQL expression evaluated against the source view',
  active         BOOLEAN
) COMMENT 'Metadata-driven ETL rules. The generic driver notebook renders these into SQL at runtime.';

-- Re-seeded every run so the config is deterministic.
DELETE FROM __CATALOG__.__SCHEMA__.etl_config WHERE target_table = 'framework_output';
INSERT INTO __CATALOG__.__SCHEMA__.etl_config VALUES
  ('framework_output', 'order_id',        'order_id',                                  true),
  ('framework_output', 'customer_id',     'customer_id',                               true),
  ('framework_output', 'revenue_usd',     'ROUND(order_net_amount, 2)',                true),
  ('framework_output', 'margin_pct',      'ROUND(order_margin / NULLIF(order_net_amount, 0) * 100, 1)', true),
  ('framework_output', 'order_month',     'DATE_FORMAT(order_date, \'yyyy-MM\')',      true),
  ('framework_output', 'size_bucket',     'CASE WHEN order_net_amount > 500 THEN \'LARGE\' WHEN order_net_amount > 100 THEN \'MEDIUM\' ELSE \'SMALL\' END', true),
  ('framework_output', 'is_cancelled',    'status_norm = \'CANCELLED\'',               true);

-- ---------------------------------------------------------------------------
-- T1-9  Hub fan-out → blast radius breadth + the business-view "data only"
-- fix. hub_orders_wide has ONE upstream and SIX downstream consumers, which is
-- the shape that used to render as an everything-to-everything mesh when edges
-- were reconstructed by cross-producting a job's inputs x outputs.
-- Expected: Impact lists 6 downstream tables; data-only view shows 6 distinct
-- (source -> target) pairs, not a mesh.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.hub_orders_wide
  COMMENT 'Fan-out hub: one upstream, six downstream consumers.'
AS SELECT * FROM __CATALOG__.__SCHEMA__.v_order_summary;

CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_daily_revenue AS
  SELECT order_date, ROUND(SUM(order_net_amount), 2) AS revenue FROM __CATALOG__.__SCHEMA__.hub_orders_wide GROUP BY order_date;
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_customer_totals AS
  SELECT customer_id, COUNT(*) AS orders, ROUND(SUM(order_net_amount), 2) AS spend FROM __CATALOG__.__SCHEMA__.hub_orders_wide GROUP BY customer_id;
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_margin_watch AS
  SELECT order_id, order_margin FROM __CATALOG__.__SCHEMA__.hub_orders_wide WHERE order_margin < 0;
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_status_counts AS
  SELECT status_norm, COUNT(*) AS n FROM __CATALOG__.__SCHEMA__.hub_orders_wide GROUP BY status_norm;
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_large_orders AS
  SELECT * FROM __CATALOG__.__SCHEMA__.hub_orders_wide WHERE order_net_amount > 500;
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.spoke_item_density AS
  SELECT order_id, item_count, ROUND(order_net_amount / NULLIF(item_count, 0), 2) AS avg_item_value FROM __CATALOG__.__SCHEMA__.hub_orders_wide;

-- ---------------------------------------------------------------------------
-- T2-10  PII columns → sensitive-column finder + governance classification.
-- Expected: /api/discover/sensitive flags email, ssn, phone, dob, credit_card,
-- ip_address; Governance panel shows the sensitivity and propagates it
-- downstream to pii_contact_export.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.pii_customers (
  customer_id  INT     COMMENT 'Surrogate key',
  full_name    STRING  COMMENT 'Customer legal name',
  email        STRING  COMMENT 'Primary contact email (PII)',
  phone        STRING  COMMENT 'Mobile number (PII)',
  ssn          STRING  COMMENT 'National insurance / social security number (highly sensitive)',
  dob          DATE    COMMENT 'Date of birth (PII)',
  credit_card  STRING  COMMENT 'Tokenised card number (PCI)',
  ip_address   STRING  COMMENT 'Last seen client IP (PII)',
  country      STRING
) COMMENT 'Synthetic PII fixture — fake values only, no real personal data.';

INSERT INTO __CATALOG__.__SCHEMA__.pii_customers
SELECT
  c.customer_id,
  c.full_name,
  c.email,
  CONCAT('+1-555-', LPAD(CAST(c.customer_id % 10000 AS STRING), 4, '0')) AS phone,
  CONCAT('000-00-', LPAD(CAST(c.customer_id % 10000 AS STRING), 4, '0'))  AS ssn,
  DATE_ADD(DATE'1970-01-01', CAST(c.customer_id % 12000 AS INT))          AS dob,
  CONCAT('tok_', MD5(CAST(c.customer_id AS STRING)))                      AS credit_card,
  CONCAT('10.0.', CAST(c.customer_id % 255 AS STRING), '.1')              AS ip_address,
  c.country
FROM __CATALOG__.silver.dim_customers c;

-- Downstream of PII → tests sensitivity propagation.
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.pii_contact_export AS
  SELECT customer_id, email, phone, country FROM __CATALOG__.__SCHEMA__.pii_customers;

-- ---------------------------------------------------------------------------
-- T2-11  DQ edge cases. The back-quoted names are the regression fixture for
-- the validator that used to reject spaces/hyphens that Unity Catalog allows
-- (rules on such columns went permanently "invalid"). The value distributions
-- give each rule type something real to fail on.
-- Expected: NOT_NULL fails on nullable_note, UNIQUE fails on dup_key, RANGE
-- fails on out_of_range_pct, REGEX fails on messy_code, and rules on
-- `Order Date` / `order-id` save and evaluate rather than 400.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.dq_edge_cases (
  `order-id`        INT     COMMENT 'Hyphenated name — legal in UC',
  `Order Date`      DATE    COMMENT 'Name with a space — legal in UC',
  nullable_note     STRING  COMMENT 'Deliberately contains NULLs (NOT_NULL rule target)',
  dup_key           INT     COMMENT 'Deliberately contains duplicates (UNIQUE rule target)',
  out_of_range_pct  DOUBLE  COMMENT 'Contains values outside 0-100 (RANGE rule target)',
  messy_code        STRING  COMMENT 'Mixed formats (REGEX rule target)'
)
-- Column names containing a space or other special character require Delta
-- column mapping. Without this, CREATE fails outright with
-- DELTA_INVALID_CHARACTERS_IN_COLUMN_NAMES — which is also the reason these
-- names are rare in the wild, and why a validator that assumes snake_case
-- looks correct until it meets a table like this one.
TBLPROPERTIES ('delta.columnMapping.mode' = 'name')
COMMENT 'Data-quality fixture: one column per rule type, plus UC-legal exotic column names.';

INSERT INTO __CATALOG__.__SCHEMA__.dq_edge_cases VALUES
  (1, DATE'2026-01-05', 'ok',   100, 12.5,  'AB-1234'),
  (2, DATE'2026-01-06', NULL,   100, 250.0, 'ab_1234'),
  (3, DATE'2026-01-07', 'ok',   101, -3.0,  'XX-9999'),
  (4, DATE'2026-01-08', NULL,   102, 99.9,  'nope'),
  (5, DATE'2026-01-09', 'ok',   102, 45.0,  'CD-5678');

-- ---------------------------------------------------------------------------
-- T2-14  Orphan: created here, never read by any tracked code.
-- Expected: /api/discover/orphans lists it; the graph shows it with
-- lineage_status = orphan.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.orphan_snapshot
  COMMENT 'Deliberate orphan — no producer reads or writes it after creation.'
AS SELECT 1 AS id, 'never referenced again' AS note;

-- ---------------------------------------------------------------------------
-- Append-only sources for the two DLT pipelines (T1-4, T1-5).
-- Deliberately CREATE ... IF NOT EXISTS + seed-once (never CREATE OR REPLACE):
-- a streaming read breaks if its source table is overwritten, so these must
-- stay append-only across re-runs of this script.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS __CATALOG__.__SCHEMA__.stream_src_orders (
  order_id     INT,
  customer_id  INT,
  order_date   DATE,
  status       STRING,
  channel      STRING
) TBLPROPERTIES (delta.enableChangeDataFeed = true)
  COMMENT 'Append-only order feed. Source for the SQL DLT streaming table.';

INSERT INTO __CATALOG__.__SCHEMA__.stream_src_orders
SELECT order_id, CAST(customer_id AS INT), CAST(order_date AS DATE), status, channel
FROM __CATALOG__.silver.fct_orders
WHERE NOT EXISTS (SELECT 1 FROM __CATALOG__.__SCHEMA__.stream_src_orders);

CREATE TABLE IF NOT EXISTS __CATALOG__.__SCHEMA__.cdc_customer_changes (
  customer_id  INT,
  full_name    STRING,
  country      STRING,
  op           STRING,
  change_ts    TIMESTAMP
) COMMENT 'Append-only change feed: two versions per customer, so SCD2 has history to close out.';

-- Two versions per customer (INSERT then UPDATE) — seeded once.
INSERT INTO __CATALOG__.__SCHEMA__.cdc_customer_changes
SELECT customer_id, full_name, country, 'INSERT', TIMESTAMP'2026-01-01 00:00:00'
FROM __CATALOG__.silver.dim_customers
WHERE NOT EXISTS (SELECT 1 FROM __CATALOG__.__SCHEMA__.cdc_customer_changes);

INSERT INTO __CATALOG__.__SCHEMA__.cdc_customer_changes
SELECT customer_id, UPPER(full_name), UPPER(country), 'UPDATE', TIMESTAMP'2026-06-01 00:00:00'
FROM __CATALOG__.silver.dim_customers
WHERE NOT EXISTS (
  SELECT 1 FROM __CATALOG__.__SCHEMA__.cdc_customer_changes WHERE op = 'UPDATE'
);
