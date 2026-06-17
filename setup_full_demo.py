#!/usr/bin/env python3
"""Comprehensive lineage demo — exercises every lineage case the app renders.

One connected graph. The main medallion runs as an explicit serverless Lakeflow
Declarative Pipeline (so pipeline lineage + DLT cost are captured); the
table->volume->table hop runs as a notebook Job (so a JOB entity + job cost are
captured, and because a declarative pipeline can't write Parquet into a volume).

Coverage:
  - VOLUME as source   : Auto Loader read_files(/Volumes/landing) -> st_orders_raw
  - VOLUME as target+back (table->volume->table): Job writes bronze to
    /Volumes/exports and reads it back into silver_orders. Exercises the
    dbfs:/Volumes write-side path normalization.
  - STREAMING_TABLE / MATERIALIZED_VIEW
  - PIPELINE entity + DLT cost (the Lakeflow pipeline)
  - JOB entity + job cost (the volume hop)
  - cross-schema  : full_demo_curated reads full_demo_raw
  - cross-catalog : catalog2.full_demo_serving reads catalog.full_demo_curated
  - VIEW
  - Delta Sharing (provider side): share with a recipient -> "shared out" badges

Mirrors setup_demo_lineage.py's exec pattern (CLI `databricks api` + serverless
SQL warehouse). Idempotent: `python setup_full_demo.py teardown` then re-run, or
run individual stages by name.
"""

import base64
import json
import subprocess
import sys
import time

PROFILE = "fe-vm-vish-aws"
WAREHOUSE_ID = "9711dcb3942dac99"
CAT1 = "ws_us_e2_vish_aws_catalog"
CAT2 = "ws_us_e2_vish_aws_catalog2"
RAW = f"{CAT1}.full_demo_raw"
CURATED = f"{CAT1}.full_demo_curated"
SERVING = f"{CAT2}.full_demo_serving"
SHARE = "full_demo_share"
RECIPIENT = "full_demo_recipient"
USER = "vishwajeet.pol@databricks.com"
HOP_NB = f"/Workspace/Users/{USER}/full_demo_volume_hop"
PIPE_NB = f"/Workspace/Users/{USER}/full_demo_pipeline_src"
PIPE_NAME = "full_demo_pipeline"

LANDING = f"/Volumes/{CAT1}/full_demo_raw/landing"
EXPORTS = f"/Volumes/{CAT1}/full_demo_raw/exports"


# ---------------------------------------------------------------- helpers
def _api(method, path, payload=None, timeout=180):
    cmd = ["databricks", "api", method, path, "--profile", PROFILE]
    if payload is not None:
        cmd += ["--json", json.dumps(payload)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    try:
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except Exception:
        return {"_raw": r.stdout, "_err": r.stderr}


def run_sql(sql, label="", poll_secs=300, quiet_fail=False):
    d = _api("post", "/api/2.0/sql/statements", {
        "warehouse_id": WAREHOUSE_ID, "statement": sql, "wait_timeout": "50s",
    })
    stmt_id = d.get("statement_id")
    state = d.get("status", {}).get("state", "UNKNOWN")
    waited = 0
    while state in ("PENDING", "RUNNING") and waited < poll_secs:
        time.sleep(5); waited += 5
        d = _api("get", f"/api/2.0/sql/statements/{stmt_id}")
        state = d.get("status", {}).get("state", "UNKNOWN")
    if state == "SUCCEEDED":
        print(f"  OK: {label}")
        return True
    err = d.get("status", {}).get("error", {}).get("message", "")
    lvl = "skip" if quiet_fail else "FAIL"
    print(f"  {lvl}: {label} — {state} — {err[:160]}")
    return False


def run_sql_batch(statements):
    return sum(run_sql(sql, label) for label, sql in statements)


def import_notebook(path, source, language="PYTHON"):
    _api("post", "/api/2.0/workspace/import", {
        "path": path, "format": "SOURCE", "language": language,
        "content": base64.b64encode(source.encode()).decode(), "overwrite": True,
    })
    print(f"  imported {path}")


def upload_volume_file(vol_path, content):
    r = subprocess.run(
        ["databricks", "fs", "cp", "-", f"dbfs:{vol_path}", "--profile", PROFILE, "--overwrite"],
        input=content, capture_output=True, text=True, timeout=120,
    )
    if r.returncode != 0:
        b = base64.b64encode(content.encode()).decode()
        _api("put", f"/api/2.0/fs/files{vol_path}?overwrite=true", {"contents": b})
    print(f"  uploaded {vol_path}")


# ---------------------------------------------------------------- stages
def stage_teardown():
    print("[0] teardown")
    # Dropping the share removes its table grants, so do it before dropping tables.
    run_sql(f"DROP SHARE IF EXISTS {SHARE}", "drop share", quiet_fail=True)
    run_sql(f"DROP RECIPIENT IF EXISTS {RECIPIENT}", "drop recipient", quiet_fail=True)
    # Drop any existing pipeline by name.
    pls = _api("get", "/api/2.0/pipelines?max_results=100").get("statuses", [])
    for p in pls:
        if p.get("name") == PIPE_NAME:
            _api("delete", f"/api/2.0/pipelines/{p['pipeline_id']}")
            print(f"  deleted pipeline {p['pipeline_id']}")
    # Drop datasets. Streaming tables and (occasionally first-pass) MVs may have
    # been materialized as plain tables, so try MV/VIEW first then fall back to
    # DROP TABLE — DROP TABLE also removes streaming tables.
    for obj, kind in [
        (f"{CURATED}.executive_summary", "MATERIALIZED VIEW"),
        (f"{RAW}.mv_orders_daily", "MATERIALIZED VIEW"),
        (f"{CURATED}.gold_orders", "MATERIALIZED VIEW"),
        (f"{CURATED}.enriched_orders", "MATERIALIZED VIEW"),
        (f"{CURATED}.vw_orders_360", "VIEW"),
        (f"{RAW}.st_orders_raw", "TABLE"),
        (f"{SERVING}.serving_orders", "TABLE"),
        (f"{CURATED}.silver_orders", "TABLE"),
        (f"{RAW}.bronze_orders", "TABLE"),
    ]:
        if not run_sql(f"DROP {kind} IF EXISTS {obj}", f"drop {obj}", quiet_fail=True):
            run_sql(f"DROP TABLE IF EXISTS {obj}", f"drop {obj} (as table)", quiet_fail=True)


def stage_schemas_volumes():
    print("[1] schemas + volumes")
    run_sql_batch([
        ("schema raw", f"CREATE SCHEMA IF NOT EXISTS {RAW}"),
        ("schema curated", f"CREATE SCHEMA IF NOT EXISTS {CURATED}"),
        ("schema serving", f"CREATE SCHEMA IF NOT EXISTS {SERVING}"),
        ("volume landing", f"CREATE VOLUME IF NOT EXISTS {RAW}.landing"),
        ("volume exports", f"CREATE VOLUME IF NOT EXISTS {RAW}.exports"),
    ])


def stage_seed_landing():
    print("[2] seed landing volume (Auto Loader source)")
    h = "order_id,customer_id,amount,order_ts\n"
    for n, lo, hi, rate in [(1, 1, 41, 13.5), (2, 41, 81, 9.25)]:
        body = h + "\n".join(
            f"{i},{100+i%7},{(i*rate)%500:.2f},2026-06-1{i%9} 1{n}:0{i%6}:00"
            for i in range(lo, hi))
        upload_volume_file(f"{LANDING}/orders/batch_00{n}.csv", body)


def stage_bronze():
    print("[3] bronze table (batch source for the volume hop)")
    run_sql(f"DROP TABLE IF EXISTS {RAW}.bronze_orders", "drop bronze", quiet_fail=True)
    run_sql(f"""
        CREATE TABLE {RAW}.bronze_orders AS
        SELECT id AS order_id, 100 + (id % 7) AS customer_id,
               round((id * 7.7) % 800, 2) AS amount,
               timestamp('2026-06-15 09:00:00') + make_interval(0,0,0,0,0,id,0) AS order_ts
        FROM range(1, 121)
    """, "bronze_orders")


def stage_volume_hop():
    print("[4] table -> volume -> table hop (notebook Job: JOB entity + cost)")
    nb = f'''# Databricks notebook source
df = spark.table("{RAW}.bronze_orders")
df.write.mode("overwrite").parquet("{EXPORTS}/orders")     # volume as TARGET (dbfs:/Volumes...)
back = spark.read.parquet("{EXPORTS}/orders")              # volume as SOURCE
back.write.mode("overwrite").saveAsTable("{CURATED}.silver_orders")
'''
    import_notebook(HOP_NB, nb)
    job = _api("post", "/api/2.2/jobs/create", {
        "name": "full_demo_volume_hop",
        "tasks": [{"task_key": "volume_hop", "notebook_task": {"notebook_path": HOP_NB}}],
    })
    job_id = job.get("job_id")
    run = _api("post", "/api/2.2/jobs/run-now", {"job_id": job_id})
    run_id = run.get("run_id")
    print(f"  job_id={job_id} run_id={run_id} — waiting…")
    state, result, waited = "PENDING", None, 0
    while state in ("PENDING", "RUNNING", "BLOCKED") and waited < 600:
        time.sleep(15); waited += 15
        st = _api("get", f"/api/2.2/jobs/runs/get?run_id={run_id}").get("state", {})
        state, result = st.get("life_cycle_state", "UNKNOWN"), st.get("result_state")
    print(f"  job finished: {state}/{result}")
    return result == "SUCCESS"


def stage_pipeline():
    print("[5] Lakeflow Declarative Pipeline (PIPELINE entity + DLT cost)")
    src = f'''-- Databricks notebook source
-- VOLUME (source) --Auto Loader--> STREAMING TABLE
CREATE OR REFRESH STREAMING TABLE st_orders_raw AS
SELECT order_id, customer_id, amount, order_ts, _metadata.file_path AS src_file
FROM STREAM read_files('{LANDING}/orders/',
  format => 'csv', header => 'true',
  schema => 'order_id INT, customer_id INT, amount DOUBLE, order_ts TIMESTAMP');

-- COMMAND ----------
-- STREAMING TABLE --> MATERIALIZED VIEW
CREATE OR REFRESH MATERIALIZED VIEW mv_orders_daily AS
SELECT date(order_ts) AS order_date, count(*) AS orders, sum(amount) AS revenue
FROM st_orders_raw GROUP BY date(order_ts);

-- COMMAND ----------
-- cross-schema: reads silver_orders (the Job's volume-hop output in full_demo_curated)
CREATE OR REFRESH MATERIALIZED VIEW {CURATED}.enriched_orders AS
SELECT order_id, customer_id, amount, order_ts
FROM {CURATED}.silver_orders;

-- COMMAND ----------
CREATE OR REFRESH MATERIALIZED VIEW {CURATED}.gold_orders AS
SELECT customer_id, count(*) AS orders, sum(amount) AS revenue
FROM {CURATED}.enriched_orders GROUP BY customer_id;

-- COMMAND ----------
-- converges both chains: batch (gold) + streaming (mv_orders_daily)
CREATE OR REFRESH MATERIALIZED VIEW {CURATED}.executive_summary AS
SELECT (SELECT sum(revenue) FROM {CURATED}.gold_orders)   AS customer_revenue,
       (SELECT sum(revenue) FROM mv_orders_daily)         AS daily_revenue,
       (SELECT count(*)     FROM {CURATED}.gold_orders)   AS customers;
'''
    import_notebook(PIPE_NB, src, language="SQL")
    pipe = _api("post", "/api/2.0/pipelines", {
        "name": PIPE_NAME,
        "serverless": True,
        "catalog": CAT1,
        "schema": "full_demo_raw",
        "development": True,
        "continuous": False,
        "libraries": [{"notebook": {"path": PIPE_NB}}],
    })
    pid = pipe.get("pipeline_id")
    if not pid:
        print(f"  FAIL create pipeline: {pipe}")
        return False
    print(f"  pipeline_id={pid} — starting update…")
    upd = _api("post", f"/api/2.0/pipelines/{pid}/updates", {"full_refresh": False})
    uid = upd.get("update_id")
    state, waited = "WAITING", 0
    while waited < 900:
        time.sleep(20); waited += 20
        info = _api("get", f"/api/2.0/pipelines/{pid}")
        latest = (info.get("latest_updates") or [{}])[0]
        state = latest.get("state", "UNKNOWN")
        if state in ("COMPLETED", "FAILED", "CANCELED"):
            break
    print(f"  pipeline update: {state}")
    return state == "COMPLETED"


def stage_serving_view():
    print("[6] cross-catalog serving + view (QUERY entity)")
    run_sql_batch([
        ("serving_orders (cross-catalog)", f"""
            CREATE OR REPLACE TABLE {SERVING}.serving_orders AS
            SELECT customer_id, orders, revenue FROM {CURATED}.gold_orders"""),
        ("vw_orders_360 (view)", f"""
            CREATE OR REPLACE VIEW {CURATED}.vw_orders_360 AS
            SELECT g.*, e.daily_revenue
            FROM {CURATED}.gold_orders g
            CROSS JOIN (SELECT daily_revenue FROM {CURATED}.executive_summary) e"""),
    ])


def stage_sharing():
    print("[7] Delta Sharing (provider side)")
    run_sql(f"CREATE SHARE IF NOT EXISTS {SHARE} COMMENT 'Full lineage demo outputs'", "create share")
    # gold_orders is a pipeline-owned MV (not shareable). Share the plain managed
    # tables in the chain so the app gets a "shared out" badge on graph nodes.
    run_sql(f"ALTER SHARE {SHARE} ADD TABLE {CURATED}.silver_orders", "share + silver_orders")
    run_sql(f"ALTER SHARE {SHARE} ADD TABLE {SERVING}.serving_orders", "share + serving_orders")
    run_sql(f"CREATE RECIPIENT IF NOT EXISTS {RECIPIENT} COMMENT 'Demo recipient'", "create recipient")
    run_sql(f"GRANT SELECT ON SHARE {SHARE} TO RECIPIENT {RECIPIENT}", "grant share")


STAGES = {
    "teardown": stage_teardown,
    "schemas": stage_schemas_volumes,
    "seed": stage_seed_landing,
    "bronze": stage_bronze,
    "volhop": stage_volume_hop,
    "pipeline": stage_pipeline,
    "serving": stage_serving_view,
    "sharing": stage_sharing,
}
DEFAULT = ["schemas", "seed", "bronze", "volhop", "pipeline", "serving", "sharing"]

if __name__ == "__main__":
    only = sys.argv[1:] or DEFAULT
    for name in (STAGES if only == ["all"] else only):
        STAGES[name]()
    print("\nDone. Lineage may take a few minutes to surface in system.access.table_lineage.")
