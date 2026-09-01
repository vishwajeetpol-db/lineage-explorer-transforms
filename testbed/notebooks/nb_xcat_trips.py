# Databricks notebook source
# T1-8  Cross-catalog read → cross-catalog lineage + external-in-scope node.
#
# Reads samples.nyctaxi.trips (a DIFFERENT catalog) into the test-bed schema.
# Expected: the graph renders samples.nyctaxi.trips as an upstream node in
# another catalog with full metadata, and column lineage crosses the catalog
# boundary (trip_miles <- trip_distance, fare_usd <- fare_amount).

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
CREATE OR REPLACE TABLE {CATALOG}.{SCHEMA}.xcat_trips_daily AS
SELECT
  CAST(tpep_pickup_datetime AS DATE)        AS pickup_date,
  pickup_zip                                AS pickup_zip,
  COUNT(*)                                  AS trip_count,
  ROUND(SUM(trip_distance), 2)              AS trip_miles,
  ROUND(SUM(fare_amount), 2)                AS fare_usd,
  ROUND(AVG(fare_amount / NULLIF(trip_distance, 0)), 4) AS fare_per_mile
FROM samples.nyctaxi.trips
GROUP BY CAST(tpep_pickup_datetime AS DATE), pickup_zip
""")
print("cross-catalog build wrote xcat_trips_daily from samples.nyctaxi.trips")
