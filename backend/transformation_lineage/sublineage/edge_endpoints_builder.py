"""Populate `lineage_edge_endpoints` from nodes + edges + raw_code.

Each row is one column-to-column hop: source column node → transformation
→ destination column node, pre-joined with the artifact's notebook path,
its expression text, and the transform_category computed at parse time.

Consumers (BFS, K-paths, API) read this table directly, avoiding runtime
joins across three tables per query.

Correctness notes
-----------------
Each derive edge is 1:1 with a parser column_mapping — its `meta_json.source_ref`
names the one source column it was derived from. We match that text against
the source-column node's `column_name` to avoid false cross-joins when an
artifact has multiple inputs and multiple outputs. `source_ref` can be either
qualified (`alias.col`) or bare (`col`), so we extract the last dotted segment
before comparing.
"""

from __future__ import annotations

import logging
import re

from pyspark.sql import SparkSession

logger = logging.getLogger(__name__)

# Validates pipeline_run_id is safe for SQL interpolation
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_run_id(pipeline_run_id: str) -> str:
    if not pipeline_run_id or not _SAFE_ID_RE.match(pipeline_run_id):
        raise ValueError(f"Invalid pipeline_run_id: {pipeline_run_id!r}")
    return pipeline_run_id


def build_edge_endpoints(
    spark: SparkSession,
    *,
    pipeline_run_id: str,
    nodes_table: str,
    edges_table: str,
    raw_code_table: str,
    endpoints_table: str,
) -> int:
    """
    Materialize column-to-column hops for the given pipeline run.

    Overwrites any existing rows for `pipeline_run_id` (idempotent per run).
    Returns the number of rows written.
    """
    safe_run_id = _validate_run_id(pipeline_run_id)

    spark.sql(
        f"DELETE FROM {endpoints_table} WHERE pipeline_run_id = '{safe_run_id}'"
    )

    # `expr_sql` is pre-populated only for sql-origin rows (expr already IS SQL).
    # PySpark-origin rows get NULL here; the AI_QUERY enricher backfills them.
    # Explicit column list — the table may have been ALTERed, so positional
    # INSERT would shuffle values into the wrong columns.
    insert_sql = f"""
    INSERT INTO {endpoints_table} (
      pipeline_run_id, src_node_id, src_fqn, src_col,
      dst_node_id, dst_fqn, dst_col,
      edge_id, artifact_id, source_path,
      expr, expr_lang, expr_sql, transform_category, materialized_at
    )
    SELECT
      derive.pipeline_run_id                                    AS pipeline_run_id,
      col_read.src_id                                           AS src_node_id,
      src_node.table_fqn                                        AS src_fqn,
      src_node.column_name                                      AS src_col,
      derive.dst_id                                             AS dst_node_id,
      dst_node.table_fqn                                        AS dst_fqn,
      dst_node.column_name                                      AS dst_col,
      derive.edge_id                                            AS edge_id,
      derive.artifact_id                                        AS artifact_id,
      raw.source_path                                           AS source_path,
      get_json_object(derive.meta_json, '$.expr')               AS expr,
      get_json_object(derive.meta_json, '$.expr_lang')          AS expr_lang,
      CASE
        WHEN get_json_object(derive.meta_json, '$.expr_lang') = 'sql'
          THEN get_json_object(derive.meta_json, '$.expr')
        ELSE NULL
      END                                                       AS expr_sql,
      get_json_object(derive.meta_json, '$.transform_category') AS transform_category,
      current_timestamp()                                       AS materialized_at
    FROM {edges_table} AS derive
    JOIN {edges_table} AS col_read
      ON derive.pipeline_run_id = col_read.pipeline_run_id
     AND derive.src_id          = col_read.dst_id
     AND derive.edge_type       = 'derive'
     AND col_read.edge_type     = 'read'
     AND col_read.src_id LIKE 'col:%'
     AND derive.src_id   LIKE 'xfm:%'
     AND derive.dst_id   LIKE 'col:%'
    JOIN {nodes_table} AS src_node
      ON src_node.pipeline_run_id = col_read.pipeline_run_id
     AND src_node.node_id         = col_read.src_id
     AND src_node.column_name     = regexp_extract(
           get_json_object(derive.meta_json, '$.source_ref'),
           '([^.]+)$',
           1
         )
    JOIN {nodes_table} AS dst_node
      ON dst_node.pipeline_run_id = derive.pipeline_run_id
     AND dst_node.node_id         = derive.dst_id
    LEFT JOIN {raw_code_table} AS raw
      ON raw.pipeline_run_id = derive.pipeline_run_id
     AND raw.extraction_id   = derive.artifact_id
    WHERE derive.pipeline_run_id = '{safe_run_id}'
    """
    spark.sql(insert_sql)

    from pyspark.sql import functions as F
    count = (
        spark.table(endpoints_table)
        .where(F.col("pipeline_run_id") == safe_run_id)
        .count()
    )
    logger.info(
        "edge_endpoints rows written pipeline_run_id=%s count=%d", pipeline_run_id, count
    )
    return int(count)
