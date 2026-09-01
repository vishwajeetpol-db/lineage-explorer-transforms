# Databricks notebook source
# T1-5  DLT AUTO CDC (apply_changes) → SCD Type 2.
#
# The SCD/CDC spec viewer (GET /api/diagnostics/scd) and the captured_cdc_specs
# table have no fixture in this workspace — nothing here uses apply_changes.
# This pipeline builds an SCD2 dimension from the append-only change feed
# __SCHEMA__.cdc_customer_changes so the viewer has a real spec to render.
#
# Expected: /api/diagnostics/scd reports scd_type=2, keys=[customer_id],
# sequence_by=change_ts for scd2_customers, and __START_AT/__END_AT columns
# appear on the table (two versions per customer, the first closed out).
import dlt
from pyspark.sql import functions as F

# Set as pipeline `configuration` by build_testbed.sh; the literals are the
# upload-time substitutions and only apply if the config key is absent.
CATALOG = spark.conf.get("bt.catalog", "__CATALOG__")
SCHEMA = spark.conf.get("bt.schema", "__SCHEMA__")

# COMMAND ----------


@dlt.view(
    name="customer_changes",
    comment="Streaming view over the append-only customer change feed.",
)
def customer_changes():
    # Must be a STREAMING source: apply_changes into a streaming table rejects
    # a batch read. The feed is append-only by construction (see 10_fixtures.sql).
    return spark.readStream.table(f"{CATALOG}.{SCHEMA}.cdc_customer_changes")


dlt.create_streaming_table(
    "scd2_customers",
    comment="SCD Type 2 customer dimension built with AUTO CDC (apply_changes).",
)

dlt.apply_changes(
    target="scd2_customers",
    source="customer_changes",
    keys=["customer_id"],
    sequence_by=F.col("change_ts"),
    stored_as_scd_type=2,
)
