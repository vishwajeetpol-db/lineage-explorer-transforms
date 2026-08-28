# Databricks notebook source
# T2-15a  Intermittent failure → run-health "Degraded" verdict.
#
# The outcome is driven by the FAIL widget so the builder can produce an exact
# pass/fail mix (2 pass + 1 fail = 67% success) in three back-to-back runs. With
# no widget it falls back to wall-clock minute parity, so a manual run from the
# Workflows UI is still usefully unpredictable.
#
# Expected: the node's health popover reads Degraded with a success rate strictly
# between 0 and 100, and the last-5-runs list shows both outcomes.
import datetime

try:
    CATALOG = dbutils.widgets.get("CATALOG")
except Exception:
    CATALOG = "__CATALOG__"
try:
    SCHEMA = dbutils.widgets.get("SCHEMA")
except Exception:
    SCHEMA = "__SCHEMA__"

try:
    should_fail = dbutils.widgets.get("FAIL").strip().lower() in ("true", "1", "yes")
    reason = "FAIL widget"
except Exception:
    should_fail = datetime.datetime.utcnow().minute % 2 == 0
    reason = "even minute"

spark.sql(
    f"CREATE TABLE IF NOT EXISTS {CATALOG}.{SCHEMA}.health_flaky_out "
    "(run_ts TIMESTAMP, ok BOOLEAN)"
)

if should_fail:
    spark.sql(f"INSERT INTO {CATALOG}.{SCHEMA}.health_flaky_out VALUES (current_timestamp(), false)")
    raise RuntimeError(f"Intentional test-bed failure ({reason}) — this job is meant to be flaky.")

spark.sql(f"INSERT INTO {CATALOG}.{SCHEMA}.health_flaky_out VALUES (current_timestamp(), true)")
print(f"flaky job succeeded ({reason} said pass)")
