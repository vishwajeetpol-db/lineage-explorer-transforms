# Databricks notebook source
# DBTITLE 1,Transformation Lineage — Pipeline Entry Point
# MAGIC %md
# MAGIC # Transformation Lineage — Pipeline Entry Point
# MAGIC
# MAGIC Self-contained notebook that runs the **transformation lineage pipeline**.
# MAGIC Used by the app's Build Service (serverless one-time job).
# MAGIC
# MAGIC **Phases:** Schema DDL → Discovery → Extraction → Versioning → Parsing (8-thread parallel) → Graph → Storage → Reconciliation → Materialization → Edge Endpoints → Expression Enrichment
# MAGIC
# MAGIC > **Note:** Runtime dependencies (`sqlparse`, `requests`, `databricks-sdk`) are provided
# MAGIC > by the job's serverless environment spec — no `%pip install` needed.

# COMMAND ----------

# DBTITLE 1,Configuration & Imports
import os
import sys
import time

# Derive current user dynamically
USER_NAME = spark.sql("SELECT current_user()").collect()[0][0]

# Locate the transformation_lineage package.
# In the combined app layout, it lives at the project root (sibling to notebooks/).
def _default_src_path():
    nb = dbutils.notebook.entry_point.getDbutils().notebook().getContext().notebookPath().get()
    # notebooks/run_pipeline → parent is project root containing transformation_lineage/
    return "/Workspace" + nb.rsplit("/notebooks/", 1)[0]

try:
    BASE_PATH = dbutils.widgets.get("SRC_PATH") or _default_src_path()
except Exception:
    BASE_PATH = os.environ.get("LINEAGE_SRC_PATH") or _default_src_path()

sys.path.insert(0, BASE_PATH)

from pyspark.sql import SparkSession
from transformation_lineage.config import LineageJobConfig
from transformation_lineage.pipeline import run_daily_pipeline

spark = SparkSession.builder.getOrCreate()

# ============ CONFIGURATION (from job parameters or defaults) ============
try:
    TARGET_CATALOG = dbutils.widgets.get("TARGET_CATALOG")
except:
    TARGET_CATALOG = "lattice_lineage"
try:
    TARGET_SCHEMA = dbutils.widgets.get("TARGET_SCHEMA")
except:
    TARGET_SCHEMA = "lineage"
try:
    _kpi_param = dbutils.widgets.get("KPI_TABLES")
    KPI_TABLES = [t.strip() for t in _kpi_param.split(",") if t.strip()]
except:
    KPI_TABLES = []

# Pipeline tuning
MAX_RUNS_PER_EXECUTION = 100
try:
    DISCOVERY_LOOKBACK_HOURS = int(dbutils.widgets.get("DISCOVERY_LOOKBACK_HOURS"))
except Exception:
    DISCOVERY_LOOKBACK_HOURS = 1080
LINEAGE_ENTITY_TYPES = ("JOB", "NOTEBOOK")

# BUILD_ONLY mode: run pipeline only (skip interactive demo phases)
try:
    BUILD_ONLY = dbutils.widgets.get("BUILD_ONLY").strip().lower() in ("true", "1", "yes")
except Exception:
    BUILD_ONLY = False

# Provide token for SDK calls
os.environ["DATABRICKS_TOKEN"] = (
    dbutils.notebook.entry_point.getDbutils().notebook().getContext().apiToken().get()
)

print(f"User: {USER_NAME}")
print(f"Source path: {BASE_PATH}")
print(f"Target: {TARGET_CATALOG}.{TARGET_SCHEMA}")
print(f"KPI Tables: {KPI_TABLES}")
print(f"Build Only: {BUILD_ONLY}")

# COMMAND ----------

# DBTITLE 1,Run Daily Lineage Pipeline
cfg = LineageJobConfig.from_dbutils(
    dbutils,
    spark,
    target_catalog=TARGET_CATALOG,
    target_schema=TARGET_SCHEMA,
    kpi_tables=KPI_TABLES,
    token_scope="lineage",
    token_key="databricks_pat",
    git_token_scope="lineage",
    git_token_key="git_http_token",
    max_runs_per_execution=MAX_RUNS_PER_EXECUTION,
    discovery_lookback_hours=DISCOVERY_LOOKBACK_HOURS,
    lineage_entity_types=LINEAGE_ENTITY_TYPES,
)

t0 = time.time()
pipeline_run_id = run_daily_pipeline(spark, cfg)
elapsed = time.time() - t0
print(f"\n{'='*60}")
print(f"  PIPELINE COMPLETE")
print(f"{'='*60}")
print(f"  Run ID  : {pipeline_run_id}")
print(f"  Elapsed : {elapsed:.1f}s")
print(f"  Target  : {TARGET_CATALOG}.{TARGET_SCHEMA}")
print(f"{'='*60}")

# COMMAND ----------

# DBTITLE 1,Sub-Lineage & Backtracking (skipped in BUILD_ONLY mode)
if not BUILD_ONLY:
    from transformation_lineage.sublineage.api import (
        find_shortest_path, backtrack_target, invalidate_graph_cache
    )
    invalidate_graph_cache()

    # Auto-derive target column from first KPI table
    _kpi_fqn = KPI_TABLES[0] if KPI_TABLES else f"{TARGET_CATALOG}.default.unknown"
    _kpi_cols = spark.sql(f"SHOW COLUMNS IN {_kpi_fqn}").collect()
    _bt_col = _kpi_cols[2][0] if len(_kpi_cols) > 2 else _kpi_cols[0][0]
    TARGET_COLUMN = f"col:{_kpi_fqn}::{_bt_col}"

    cfg_query = LineageJobConfig(
        target_catalog=TARGET_CATALOG,
        target_schema=TARGET_SCHEMA,
        kpi_tables=(),
    )

    # Backtracking
    bt_result = backtrack_target(
        spark, cfg_query,
        target=TARGET_COLUMN,
        max_depth=6,
        exclude_table_patterns=[],
        include_categories=None,
    )
    levels = bt_result.get("levels") or []
    print(f"\nBacktrack: {len(levels)} levels from {TARGET_COLUMN}")
    for lvl in levels:
        depth = lvl.get("depth", "?")
        columns = lvl.get("columns") or []
        print(f"  Level {depth}: {len(columns)} column(s)")
else:
    print("BUILD_ONLY mode — skipping sub-lineage & backtracking.")

# COMMAND ----------


