"""Deterministic cache keys + read/write for `lineage_sublineage_cache`.

Each cached entry is keyed by
    sha256(src | dst | mode | max_depth | max_paths | version_hash)
where `version_hash` is a fingerprint of the edge adjacency for the pipeline
run. Any upstream change — a new artifact, a changed expression, a retired
edge — shifts the fingerprint and the previous cache entries become dead
(never read). Writes always append a new row with the fresh key; old rows
are left in place for audit (Delta retention handles pruning).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from pyspark.sql import SparkSession
from pyspark.sql import functions as F
from pyspark.sql.types import IntegerType, StringType, StructField, StructType, TimestampType


def compute_version_hash(spark: SparkSession, *, endpoints_table: str, pipeline_run_id: str) -> str:
    """
    Fingerprint the edge set for a pipeline run.

    Hash is sha256 over the sorted list of edge_ids in the given run. Stable
    across re-reads; changes whenever any hop is added, removed, or the
    underlying expression changes (because edge_id derives from content).
    """
    rows = (
        spark.table(endpoints_table)
        .where(F.col("pipeline_run_id") == pipeline_run_id)
        .select("edge_id")
        .orderBy("edge_id")
        .collect()
    )
    h = hashlib.sha256()
    for r in rows:
        h.update((r["edge_id"] or "").encode())
        h.update(b"|")
    return h.hexdigest()[:32]


def compute_cache_key(
    *,
    src_node_id: str,
    dst_node_id: str,
    mode: str,
    max_depth: int,
    max_paths: int,
    version_hash: str,
) -> str:
    """Deterministic sha256 key for a (src, dst, mode, limits, version) tuple."""
    canonical = "|".join(
        [
            src_node_id,
            dst_node_id,
            mode,
            str(max_depth),
            str(max_paths),
            version_hash,
        ]
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def read_cache(
    spark: SparkSession,
    *,
    cache_table: str,
    cache_key: str,
) -> dict | None:
    """Return the most recent cached result for a key, or None if missing."""
    rows = (
        spark.table(cache_table)
        .where(F.col("cache_key") == cache_key)
        .orderBy(F.col("computed_at").desc())
        .select("paths_json", "hop_count", "path_count", "computed_at",
                "src_node_id", "dst_node_id", "mode", "version_hash")
        .limit(1)
        .collect()
    )
    if not rows:
        return None
    r = rows[0]
    return {
        "src_node_id": r["src_node_id"],
        "dst_node_id": r["dst_node_id"],
        "mode": r["mode"],
        "hop_count": r["hop_count"],
        "path_count": r["path_count"],
        "version_hash": r["version_hash"],
        "paths": json.loads(r["paths_json"]) if r["paths_json"] else [],
        "computed_at": r["computed_at"],
        "cache_hit": True,
    }


def write_cache(
    spark: SparkSession,
    *,
    cache_table: str,
    pipeline_run_id: str,
    cache_key: str,
    src_node_id: str,
    dst_node_id: str,
    mode: str,
    hop_count: int,
    path_count: int,
    paths_json: str,
    version_hash: str,
) -> None:
    """Append one cache row. Existing rows for the same key are left in place."""
    _schema = StructType([
        StructField("pipeline_run_id", StringType(),    True),
        StructField("cache_key",        StringType(),    True),
        StructField("src_node_id",      StringType(),    True),
        StructField("dst_node_id",      StringType(),    True),
        StructField("mode",             StringType(),    True),
        StructField("hop_count",        IntegerType(),   True),
        StructField("path_count",       IntegerType(),   True),
        StructField("paths_json",       StringType(),    True),
        StructField("version_hash",     StringType(),    True),
        StructField("computed_at",      TimestampType(), True),
    ])
    row = (
        pipeline_run_id, cache_key, src_node_id, dst_node_id, mode,
        int(hop_count), int(path_count), paths_json, version_hash,
        datetime.now(timezone.utc),
    )
    (
        spark.createDataFrame([row], schema=_schema)
        .write.format("delta")
        .mode("append")
        .saveAsTable(cache_table)
    )
