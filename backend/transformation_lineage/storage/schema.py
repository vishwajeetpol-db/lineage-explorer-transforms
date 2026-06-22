"""Delta table DDL for lineage storage (Unity Catalog managed tables).

Cost optimizations:
  - Liquid clustering on pipeline_run_id for fast pruning on all hot-path queries
  - Targeted OPTIMIZE after schema creation for initial compaction
  - Minimal column types (no over-wide schemas)
"""

from __future__ import annotations

import logging

from pyspark.sql import SparkSession

from transformation_lineage.config import LineageJobConfig

logger = logging.getLogger(__name__)


def _fq(cfg: LineageJobConfig, name: str) -> str:
    return cfg.fully_qualified(name)


def ensure_lineage_schema(spark: SparkSession, cfg: LineageJobConfig) -> dict[str, str]:
    """
    Create catalog.schema if needed and idempotent Delta tables.

    Returns mapping of logical name -> three-part table name.
    """
    catalog_exists = spark.sql(
        f"SHOW CATALOGS LIKE '{cfg.target_catalog}'"
    ).count() > 0
    if not catalog_exists:
        spark.sql(f"CREATE CATALOG IF NOT EXISTS {cfg.target_catalog}")
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {cfg.target_catalog}.{cfg.target_schema}")

    tables = {
        "raw_code": _fq(cfg, "lineage_raw_code"),
        "parse_metrics": _fq(cfg, "lineage_parse_metrics"),
        "nodes": _fq(cfg, "lineage_nodes"),
        "edges": _fq(cfg, "lineage_edges"),
        "code_versions": _fq(cfg, "lineage_code_versions"),
        "extraction_reports": _fq(cfg, "lineage_extraction_reports"),
        "graph_cache": _fq(cfg, "lineage_graph_cache"),
        "reconciliation": _fq(cfg, "lineage_reconciliation"),
        "edge_endpoints": _fq(cfg, "lineage_edge_endpoints"),
        "sublineage_cache": _fq(cfg, "lineage_sublineage_cache"),
        "pyspark_to_sql_cache": _fq(cfg, "lineage_pyspark_to_sql_cache"),
        "notebook_path_cache": _fq(cfg, "lineage_notebook_path_cache"),
    }

    # High-volume tables get CLUSTER BY for predicate pushdown on pipeline_run_id.
    # This eliminates full-table scans when querying recent runs.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["raw_code"]} (
          pipeline_run_id STRING,
          extraction_id STRING,
          run_id BIGINT,
          job_id BIGINT,
          task_key STRING,
          source_kind STRING,
          source_path STRING,
          git_commit STRING,
          language STRING,
          content_sha256 STRING,
          raw_source STRING,
          normalized_cells_json STRING,
          extracted_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["parse_metrics"]} (
          pipeline_run_id STRING,
          extraction_id STRING,
          language STRING,
          statements_parsed INT,
          statements_skipped INT,
          table_ref_count INT,
          mapping_count INT,
          warnings STRING,
          parsed_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["nodes"]} (
          pipeline_run_id STRING,
          node_id STRING,
          node_type STRING,
          label STRING,
          table_fqn STRING,
          column_name STRING,
          artifact_id STRING,
          meta_json STRING,
          created_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id, node_type)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["edges"]} (
          pipeline_run_id STRING,
          edge_id STRING,
          src_id STRING,
          dst_id STRING,
          edge_type STRING,
          artifact_id STRING,
          meta_json STRING,
          created_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id, edge_type)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["code_versions"]} (
          pipeline_run_id STRING,
          extraction_id STRING,
          content_sha256 STRING,
          is_new_version BOOLEAN,
          recorded_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (extraction_id)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["extraction_reports"]} (
          pipeline_run_id STRING,
          report_json STRING,
          recorded_at TIMESTAMP
        ) USING DELTA
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["graph_cache"]} (
          pipeline_run_id STRING,
          cache_key STRING,
          kpi_table_fqn STRING,
          graph_json STRING,
          version_hash STRING,
          materialized_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["reconciliation"]} (
          pipeline_run_id STRING,
          system_lineage_row_count BIGINT,
          parsed_edge_count_run BIGINT,
          note STRING,
          recorded_at TIMESTAMP
        ) USING DELTA
        """
    )

    # Primary query table for sublineage — heavily read during path queries.
    # Clustered by pipeline_run_id for fast partition pruning.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["edge_endpoints"]} (
          pipeline_run_id STRING,
          src_node_id STRING,
          src_fqn STRING,
          src_col STRING,
          dst_node_id STRING,
          dst_fqn STRING,
          dst_col STRING,
          edge_id STRING,
          artifact_id STRING,
          source_path STRING,
          expr STRING,
          expr_lang STRING,
          expr_sql STRING,
          transform_category STRING,
          materialized_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (pipeline_run_id)
        """
    )

    # Idempotent ALTER for tables that already exist from older schema.
    for col_def in ("expr_lang STRING", "expr_sql STRING"):
        try:
            spark.sql(
                f"ALTER TABLE {tables['edge_endpoints']} ADD COLUMNS ({col_def})"
            )
        except Exception:
            pass

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["pyspark_to_sql_cache"]} (
          expr_sha STRING,
          pyspark_expr STRING,
          sql_expr STRING,
          model STRING,
          translated_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (expr_sha)
        """
    )

    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["sublineage_cache"]} (
          pipeline_run_id STRING,
          cache_key STRING,
          src_node_id STRING,
          dst_node_id STRING,
          mode STRING,
          hop_count INT,
          path_count INT,
          paths_json STRING,
          version_hash STRING,
          computed_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (cache_key)
        """
    )

    # notebook_id -> workspace path. Resolving via system.access.audit is a very
    # expensive full-log scan; caching makes it a one-time cost per notebook.
    spark.sql(
        f"""
        CREATE TABLE IF NOT EXISTS {tables["notebook_path_cache"]} (
          notebook_id STRING,
          nb_path STRING,
          resolved_at TIMESTAMP
        ) USING DELTA
        CLUSTER BY (notebook_id)
        """
    )

    return tables
