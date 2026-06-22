"""Append-only Delta writers for lineage facts."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Sequence

from pyspark.sql import Row, SparkSession
from pyspark.sql.types import (
    IntegerType, LongType, StringType, StructField, StructType, TimestampType,
)

from transformation_lineage.types import ExtractedArtifact, LineageEdgeRecord, LineageNodeRecord

_RAW_CODE_SCHEMA = StructType([
    StructField("pipeline_run_id", StringType(), False),
    StructField("extraction_id", StringType(), False),
    StructField("run_id", LongType(), False),
    StructField("job_id", LongType(), True),
    StructField("task_key", StringType(), True),
    StructField("source_kind", StringType(), False),
    StructField("source_path", StringType(), True),
    StructField("git_commit", StringType(), True),
    StructField("language", StringType(), False),
    StructField("content_sha256", StringType(), False),
    StructField("raw_source", StringType(), False),
    StructField("normalized_cells_json", StringType(), False),
    StructField("extracted_at", TimestampType(), False),
])

_PARSE_METRICS_SCHEMA = StructType([
    StructField("pipeline_run_id", StringType(), False),
    StructField("extraction_id", StringType(), False),
    StructField("language", StringType(), False),
    StructField("statements_parsed", IntegerType(), False),
    StructField("statements_skipped", IntegerType(), False),
    StructField("table_ref_count", IntegerType(), False),
    StructField("mapping_count", IntegerType(), False),
    StructField("warnings", StringType(), False),
    StructField("parsed_at", TimestampType(), False),
])

_NODE_SCHEMA = StructType([
    StructField("pipeline_run_id", StringType(), False),
    StructField("node_id", StringType(), False),
    StructField("node_type", StringType(), False),
    StructField("label", StringType(), False),
    StructField("table_fqn", StringType(), True),
    StructField("column_name", StringType(), True),
    StructField("artifact_id", StringType(), True),
    StructField("meta_json", StringType(), False),
    StructField("created_at", TimestampType(), False),
])

_EDGE_SCHEMA = StructType([
    StructField("pipeline_run_id", StringType(), False),
    StructField("edge_id", StringType(), False),
    StructField("src_id", StringType(), False),
    StructField("dst_id", StringType(), False),
    StructField("edge_type", StringType(), False),
    StructField("artifact_id", StringType(), True),
    StructField("meta_json", StringType(), False),
    StructField("created_at", TimestampType(), False),
])

_REPORT_SCHEMA = StructType([
    StructField("pipeline_run_id", StringType(), False),
    StructField("report_json", StringType(), False),
    StructField("recorded_at", TimestampType(), False),
])


def write_extracted_artifacts(
    spark: SparkSession,
    table_fqn: str,
    pipeline_run_id: str,
    artifacts: Sequence[ExtractedArtifact],
    *,
    content_sha256_fn: Callable[[str], str],
) -> None:
    rows: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for a in artifacts:
        rows.append({
            "pipeline_run_id": pipeline_run_id,
            "extraction_id": a.extraction_id,
            "run_id": a.run_id,
            "job_id": a.job_id,
            "task_key": a.task_key,
            "source_kind": a.source_kind,
            "source_path": a.source_path,
            "git_commit": a.git_commit,
            "language": a.language,
            "content_sha256": content_sha256_fn(a.raw_source),
            "raw_source": a.raw_source,
            "normalized_cells_json": a.normalized_cells_json,
            "extracted_at": now,
        })
    if not rows:
        return
    spark.createDataFrame(rows, schema=_RAW_CODE_SCHEMA).write.format("delta").mode("append").saveAsTable(table_fqn)


def _parse_metrics_row(pipeline_run_id: str, extraction_id: str, parse: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "pipeline_run_id": pipeline_run_id,
        "extraction_id": extraction_id,
        "language": str(parse.get("language") or ""),
        "statements_parsed": int(parse.get("statements_parsed") or 0),
        "statements_skipped": int(parse.get("statements_skipped") or 0),
        "table_ref_count": len(parse.get("table_references") or []),
        "mapping_count": len(parse.get("column_mappings") or []),
        "warnings": json.dumps(parse.get("warnings") or []),
        "parsed_at": now,
    }


def write_parse_metrics(
    spark: SparkSession,
    table_fqn: str,
    pipeline_run_id: str,
    extraction_id: str,
    parse: dict[str, Any],
) -> None:
    now = datetime.now(timezone.utc)
    row = _parse_metrics_row(pipeline_run_id, extraction_id, parse, now)
    spark.createDataFrame([row], schema=_PARSE_METRICS_SCHEMA).write.format("delta").mode("append").saveAsTable(table_fqn)


def write_parse_metrics_batch(
    spark: SparkSession,
    table_fqn: str,
    pipeline_run_id: str,
    metrics: Sequence[tuple[str, dict[str, Any]]],
) -> None:
    """Write parse metrics for many artifacts in a single Delta append."""
    if not metrics:
        return
    now = datetime.now(timezone.utc)
    rows = [_parse_metrics_row(pipeline_run_id, eid, parse, now) for eid, parse in metrics]
    spark.createDataFrame(rows, schema=_PARSE_METRICS_SCHEMA).write.format("delta").mode("append").saveAsTable(table_fqn)


def write_graph_records(
    spark: SparkSession,
    nodes_table: str,
    edges_table: str,
    pipeline_run_id: str,
    nodes: Sequence[LineageNodeRecord],
    edges: Sequence[LineageEdgeRecord],
) -> None:
    now = datetime.now(timezone.utc)
    nrows = [{
        "pipeline_run_id": pipeline_run_id,
        "node_id": n.node_id, "node_type": n.node_type,
        "label": n.label, "table_fqn": n.table_fqn,
        "column_name": n.column_name, "artifact_id": n.artifact_id,
        "meta_json": n.meta_json, "created_at": now,
    } for n in nodes]
    erows = [{
        "pipeline_run_id": pipeline_run_id,
        "edge_id": e.edge_id, "src_id": e.src_id,
        "dst_id": e.dst_id, "edge_type": e.edge_type,
        "artifact_id": e.artifact_id, "meta_json": e.meta_json,
        "created_at": now,
    } for e in edges]
    if nrows:
        spark.createDataFrame(nrows, schema=_NODE_SCHEMA).write.format("delta").mode("append").saveAsTable(nodes_table)
    if erows:
        spark.createDataFrame(erows, schema=_EDGE_SCHEMA).write.format("delta").mode("append").saveAsTable(edges_table)


def write_json_report(spark: SparkSession, table_fqn: str, pipeline_run_id: str, report: dict[str, Any]) -> None:
    row = {
        "pipeline_run_id": pipeline_run_id,
        "report_json": json.dumps(report, default=str),
        "recorded_at": datetime.now(timezone.utc),
    }
    spark.createDataFrame([row], schema=_REPORT_SCHEMA).write.format("delta").mode("append").saveAsTable(table_fqn)
