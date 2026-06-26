"""Content hashing and version rows for extracted code (PRD §6.1.6).

Cost optimizations:
  - batch_check_versions: single query checks all artifacts vs N individual queries
  - batch_record_versions: single DataFrame write vs N individual writes
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from pyspark.sql import Row, SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import BooleanType, StringType, StructField, StructType, TimestampType
from pyspark.errors import AnalysisException


# Bump whenever the parser / graph-builder changes how it extracts column
# lineage from UNCHANGED source. The version-check key folds this in, so a
# deployed parser fix forces a one-time re-parse of every artifact even when its
# source content is byte-for-byte identical. Without this, early-termination
# (pipeline.py) skips re-parsing unchanged content and the fix silently no-ops —
# this is exactly why the streaming-table parser fixes never took effect.
#
# History:
#   1  implicit baseline (content-only key)
#   2  STREAM(...) unwrap + unqualified-column attribution; @dlt.table extraction
#   3  self-loop guard; sqlparse pinned to 0.4.4
#   4  edge-endpoints join pins source by node id (kills same-column cross-joins)
#   5  CTE resolution (WITH ... AS) — resolve cte.col through the CTE to its base source
PARSER_VERSION = "5"


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def version_token(raw_source: str) -> str:
    """Change-detection key = hash of (parser version + source content).

    Distinct from ``content_sha256`` (the pure content hash stored as code
    provenance): this token gates whether an artifact is re-parsed. Bumping
    ``PARSER_VERSION`` invalidates every token, triggering a full re-parse on the
    next run so engine improvements reach already-seen objects.
    """
    return content_sha256(f"parser_v{PARSER_VERSION}\x00{raw_source}")


def is_new_content_version(spark: SparkSession, versions_table_fqn: str, extraction_id: str, sha256: str) -> bool:
    """Return True if this extraction_id has never been seen or the hash changed."""
    try:
        df = spark.table(versions_table_fqn).where(F.col("extraction_id") == extraction_id)
    except AnalysisException:
        return True
    if df.limit(1).count() == 0:
        return True
    last = (
        df.orderBy(F.col("recorded_at").desc())
        .select("content_sha256")
        .limit(1)
        .collect()
    )
    if not last:
        return True
    return str(last[0]["content_sha256"]) != str(sha256)


def batch_check_versions(
    spark: SparkSession,
    versions_table_fqn: str,
    sha_map: dict[str, str],
) -> set[str]:
    """Check all extraction_ids in ONE query. Returns set of IDs that are new or changed."""
    if not sha_map:
        return set()

    try:
        extraction_ids = list(sha_map.keys())
        latest_versions = (
            spark.table(versions_table_fqn)
            .where(F.col("extraction_id").isin(extraction_ids))
            .groupBy("extraction_id")
            .agg(F.max_by("content_sha256", "recorded_at").alias("latest_sha"))
            .collect()
        )
    except AnalysisException:
        return set(sha_map.keys())

    known_shas = {row["extraction_id"]: row["latest_sha"] for row in latest_versions}

    new_ids: set[str] = set()
    for eid, sha in sha_map.items():
        if eid not in known_shas or known_shas[eid] != sha:
            new_ids.add(eid)

    return new_ids


def batch_record_versions(
    spark: SparkSession,
    versions_table_fqn: str,
    *,
    pipeline_run_id: str,
    sha_map: dict[str, str],
    new_version_ids: set[str],
) -> None:
    """Write all version records in a single batch instead of N individual writes."""
    if not sha_map:
        return

    now = datetime.now(timezone.utc)
    rows = [
        {
            "pipeline_run_id": pipeline_run_id,
            "extraction_id": eid,
            "content_sha256": sha,
            "is_new_version": eid in new_version_ids,
            "recorded_at": now,
        }
        for eid, sha in sha_map.items()
    ]

    schema = StructType([
        StructField("pipeline_run_id", StringType(), False),
        StructField("extraction_id", StringType(), False),
        StructField("content_sha256", StringType(), False),
        StructField("is_new_version", BooleanType(), False),
        StructField("recorded_at", TimestampType(), False),
    ])

    spark.createDataFrame(rows, schema=schema).write.format("delta").mode("append").saveAsTable(versions_table_fqn)


def record_version_row(
    spark: SparkSession,
    versions_table_fqn: str,
    *,
    pipeline_run_id: str,
    extraction_id: str,
    sha256: str,
    is_new_version: bool,
) -> None:
    """Legacy single-row writer. Prefer batch_record_versions()."""
    row = Row(
        pipeline_run_id=pipeline_run_id,
        extraction_id=extraction_id,
        content_sha256=sha256,
        is_new_version=is_new_version,
        recorded_at=datetime.now(timezone.utc),
    )
    spark.createDataFrame([row]).write.format("delta").mode("append").saveAsTable(versions_table_fqn)
