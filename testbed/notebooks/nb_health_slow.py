# Databricks notebook source
# T2-15c  Duration/cost spike → per-run cost spike flag.
# Idles for SLEEP_SECONDS (default 240) so one run costs visibly more than its
# siblings. Serverless bills for wall-clock, so keep this short: 4 minutes is
# enough for the spike flag to trip and costs only a few cents.
# Expected: the health popover flags this run's cost as a spike vs the median.
import time

try:
    SLEEP_SECONDS = int(dbutils.widgets.get("SLEEP_SECONDS"))
except Exception:
    SLEEP_SECONDS = 240

print(f"idling {SLEEP_SECONDS}s to create a duration/cost outlier")
time.sleep(SLEEP_SECONDS)
print("done")
