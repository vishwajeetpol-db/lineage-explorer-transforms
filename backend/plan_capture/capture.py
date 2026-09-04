"""Capture a DataFrame's Analyzed Logical Plan into a versioned Delta table.

Design constraints proven out in the spike (see the source project's
FINDINGS.md, lineage-plan-capture):
- Method B (`df.explain(mode="extended")`) is the ONE extraction path that works
  on both classic Spark and serverless Spark Connect.
- Capture must be NON-FATAL: a failure here must never break a production write.
- Versioned + deduped by content hash, mirroring the lineage app's existing
  `producer_analysis` table semantics.

Vendored into the combined app (backend/plan_capture/) with one change from
upstream: DEFAULT_TABLE / DEFAULT_SPEC_TABLE now fall back to this app's
LINEAGE_CATALOG / LINEAGE_SCHEMA convention (lattice_lineage.lineage by
default) instead of the plugin's original hardcoded
`pritam_demo_workspace_catalog.lineage_explorer`. LINEAGE_CAPTURE_TABLE /
LINEAGE_CDC_SPEC_TABLE still take precedence when set explicitly, unchanged
from upstream.
"""
from __future__ import annotations
import io
import contextlib
import hashlib
import os
import re

_EXPR_ID_RE = re.compile(r"#\d+L?")

_LINEAGE_CATALOG = os.environ.get("LINEAGE_CATALOG", "lattice_lineage")
_LINEAGE_SCHEMA = os.environ.get("LINEAGE_SCHEMA", "lineage")

DEFAULT_TABLE = os.environ.get(
    "LINEAGE_CAPTURE_TABLE",
    f"{_LINEAGE_CATALOG}.{_LINEAGE_SCHEMA}.captured_plans",
)
DEFAULT_SPEC_TABLE = os.environ.get(
    "LINEAGE_CDC_SPEC_TABLE",
    f"{_LINEAGE_CATALOG}.{_LINEAGE_SCHEMA}.captured_cdc_specs",
)

_PLAN_SCHEMA = (
    "struct<target_full_name:string,version:int,plan_hash:string,"
    "analyzed_plan:string,captured_via:string,dbr_version:string,"
    "pipeline_id:string,job_id:string,run_id:string,captured_by:string>"
)
_SPEC_SCHEMA = (
    "struct<target_full_name:string,version:int,kind:string,source:string,"
    "keys:string,sequence_by:string,scd_type:int,except_cols:string,"
    "pipeline_id:string>"
)


def _active_spark(spark):
    if spark is not None:
        return spark
    try:
        from pyspark.sql import SparkSession
        return SparkSession.getActiveSession() or SparkSession.builder.getOrCreate()
    except Exception:
        return None


def _conf(spark, key):
    try:
        return spark.conf.get(key)
    except Exception:
        return None


def analyzed_plan(df) -> str:
    """The '== Analyzed Logical Plan ==' section (cross-mode: classic + Connect)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        df.explain(mode="extended")
    full = buf.getvalue()
    marker = "== Analyzed Logical Plan =="
    if marker not in full:
        return full.strip()
    out = []
    for ln in full.split(marker, 1)[1].splitlines():
        s = ln.strip()
        if s.startswith("== ") and s.endswith(" =="):
            break
        out.append(ln)
    return "\n".join(out).strip()


def plan_hash(plan: str) -> str:
    """Content hash over the plan with `#exprId`s stripped.

    Spark regenerates attribute exprIds (`amount#11941`) on every run, so hashing
    the raw plan text would never match across runs and dedup would never fire.
    Normalizing them out makes a re-run of unchanged code hash identically.
    """
    norm = re.sub(r"\s+", " ", _EXPR_ID_RE.sub("", plan)).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def _existing(spark, table, target):
    """(latest_version:int, {plan_hash,...}) for target; (0, set()) if none/error."""
    try:
        rows = (spark.table(table)
                .filter(f"target_full_name = '{target}'")
                .select("version", "plan_hash")
                .collect())
        if not rows:
            return 0, set()
        return max(int(r["version"] or 0) for r in rows), {r["plan_hash"] for r in rows}
    except Exception:
        return 0, set()


def capture(df, target, *, spark=None, captured_via="explain_extended",
            table=None, job_id=None, pipeline_id=None, run_id=None, **meta):
    """Append df's analyzed plan to the captured_plans table. NON-FATAL.

    - Dedupes by (target, plan_hash): an unchanged plan is not re-written.
    - A changed plan appends version = max(version)+1.
    Returns a small status dict; callers normally ignore it.

    Producer ids (job_id / pipeline_id / run_id) are normally read from Spark
    confs, but on serverless (Spark Connect) those confs aren't exposed — pass
    them explicitly so the lineage app can still attribute the plan to the exact
    producer. Explicit values win over the conf-derived ones.
    """
    try:
        spark = _active_spark(spark)
        if spark is None:
            return {"ok": False, "reason": "no SparkSession"}
        table = table or DEFAULT_TABLE
        plan = analyzed_plan(df)
        h = plan_hash(plan)
        latest, hashes = _existing(spark, table, target)
        if h in hashes:
            return {"ok": True, "status": "unchanged", "version": latest, "plan_hash": h}
        version = latest + 1
        from pyspark.sql import functions as F
        row = {
            "target_full_name": target,
            "version": int(version),
            "plan_hash": h,
            "analyzed_plan": plan,
            "captured_via": captured_via,
            "dbr_version": _conf(spark, "spark.databricks.clusterUsageTags.sparkVersion"),
            "pipeline_id": pipeline_id or _conf(spark, "pipelines.id"),
            # job_id is the JOB entity_id in system.access.table_lineage (run_id is
            # per-run and won't match); capture both so JOB producers resolve.
            # Explicit overrides matter on serverless where the confs are empty.
            "job_id": job_id or _conf(spark, "spark.databricks.job.id"),
            "run_id": run_id or _conf(spark, "spark.databricks.job.runId"),
            "captured_by": _conf(spark, "spark.databricks.clusterUsageTags.user"),
        }
        (spark.createDataFrame([row], schema=_PLAN_SCHEMA)
            .withColumn("captured_at", F.current_timestamp())
            .write.format("delta").mode("append").option("mergeSchema", "true")
            .saveAsTable(table))
        return {"ok": True, "status": "captured", "version": version, "plan_hash": h}
    except Exception as e:  # never propagate
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}


def capture_cdc_spec(target, *, source, keys, sequence_by, scd_type=1,
                     except_cols=None, spark=None, table=None, **_):
    """Record an apply_changes / AUTO CDC spec (the target has no plan). NON-FATAL.

    CDC target column lineage = source columns (passthrough) + SCD metadata, plus
    the keys/sequence_by/except declared here — config, not a query plan.
    """
    try:
        import json
        spark = _active_spark(spark)
        if spark is None:
            return {"ok": False, "reason": "no SparkSession"}
        table = table or DEFAULT_SPEC_TABLE
        latest, _ = _existing(spark, table, target)
        from pyspark.sql import functions as F
        row = {
            "target_full_name": target,
            "version": int(latest + 1),
            "kind": f"apply_changes_scd{int(scd_type)}",
            "source": source,
            "keys": json.dumps(list(keys)),
            "sequence_by": str(sequence_by),
            "scd_type": int(scd_type),
            "except_cols": json.dumps(list(except_cols or [])),
            "pipeline_id": _conf(spark, "pipelines.id"),
        }
        (spark.createDataFrame([row], schema=_SPEC_SCHEMA)
            .withColumn("captured_at", F.current_timestamp())
            .write.format("delta").mode("append").option("mergeSchema", "true")
            .saveAsTable(table))
        return {"ok": True, "status": "captured", "version": row["version"]}
    except Exception as e:
        return {"ok": False, "reason": f"{type(e).__name__}: {e}"}
