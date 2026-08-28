-- =============================================================================
-- T2-12  Schema evolution → schema-change / breaking-change detector.
-- Recreated and re-evolved on every run, so each build emits a fresh sequence
-- of schema-change events (additive, then widening, then a BREAKING drop).
-- Expected: GET /api/diagnostics/schema-changes reports the ADD as non-breaking
-- and the DROP of legacy_ref as breaking, on __SCHEMA__.evolving_orders.
-- =============================================================================
CREATE OR REPLACE TABLE __CATALOG__.__SCHEMA__.evolving_orders (
  order_id     INT,
  customer_id  INT,
  amount       INT       COMMENT 'Starts as INT, widened to DOUBLE below',
  legacy_ref   STRING    COMMENT 'Dropped below — the breaking change'
)
-- Both later ALTERs are gated behind table features: type widening needs
-- delta.enableTypeWidening, and DROP COLUMN needs column mapping (otherwise
-- DELTA_UNSUPPORTED_DROP_COLUMN). Without these the fixture stops half-built.
TBLPROPERTIES (
  'delta.enableTypeWidening' = 'true',
  'delta.columnMapping.mode' = 'name'
)
COMMENT 'Schema-evolution fixture: additive change, type widening, then a breaking drop.';

INSERT INTO __CATALOG__.__SCHEMA__.evolving_orders VALUES (1, 10, 100, 'L-1'), (2, 11, 250, 'L-2');

-- 1. additive (non-breaking)
ALTER TABLE __CATALOG__.__SCHEMA__.evolving_orders ADD COLUMNS (currency STRING COMMENT 'Added after initial load');
ALTER TABLE __CATALOG__.__SCHEMA__.evolving_orders ADD COLUMNS (settled_at TIMESTAMP);

-- 2. type widening (non-breaking for readers, still a schema change)
ALTER TABLE __CATALOG__.__SCHEMA__.evolving_orders ALTER COLUMN amount TYPE BIGINT;

-- 3. breaking: a consumer selecting legacy_ref now fails
ALTER TABLE __CATALOG__.__SCHEMA__.evolving_orders DROP COLUMN legacy_ref;
