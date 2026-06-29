"""Translate PySpark column expressions to SQL via Databricks `ai_query`.

Runs after `build_edge_endpoints`. For every row in `lineage_edge_endpoints`
where `expr_lang = 'pyspark'` and `expr_sql IS NULL`, this stage:

  1. Computes `sha256(expr)` to dedupe identical PySpark snippets.
  2. Skips snippets already in `lineage_pyspark_to_sql_cache`.
  3. Calls `ai_query(<model>, <prompt>)` for the remaining unique snippets,
     writing each translation to the cache table exactly once.
  4. MERGEs the cached SQL form back into `lineage_edge_endpoints.expr_sql`.

The cache key is the snippet content hash, not the run id, so translations
persist across pipeline runs and cost is paid once per unique expression.
"""

from __future__ import annotations

import logging
import re

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "databricks-meta-llama-3-3-70b-instruct"

_PROMPT = (
    "Translate this PySpark column expression to the equivalent Spark SQL "
    "expression. Return ONLY the SQL expression - no prose, no code fences, "
    "no markdown, no leading or trailing whitespace. PySpark: "
)

# Validates pipeline_run_id is safe for SQL interpolation
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_run_id(pipeline_run_id: str) -> str:
    if not pipeline_run_id or not _SAFE_ID_RE.match(pipeline_run_id):
        raise ValueError(f"Invalid pipeline_run_id: {pipeline_run_id!r}")
    return pipeline_run_id


def enrich_pyspark_expressions(
    spark: SparkSession,
    *,
    pipeline_run_id: str,
    endpoints_table: str,
    cache_table: str,
    model: str = DEFAULT_MODEL,
    max_expr_chars: int = 2000,
) -> dict[str, int]:
    """Translate PySpark `expr` -> SQL `expr_sql` for the given pipeline run.

    Returns counts: pending (rows needing translation), translated (new
    snippets sent to LLM), backfilled (rows MERGE-updated).
    """
    safe_run_id = _validate_run_id(pipeline_run_id)

    # Use DataFrame API for the initial filter to avoid SQL injection
    pending = (
        spark.table(endpoints_table)
        .where(F.col("pipeline_run_id") == safe_run_id)
        .where(F.col("expr_lang") == "pyspark")
        .where(F.col("expr_sql").isNull())
        .where(F.col("expr").isNotNull())
        .where(F.length(F.col("expr")) <= max_expr_chars)
        .select(
            F.sha2(F.col("expr"), 256).alias("expr_sha"),
            F.col("expr").alias("pyspark_expr"),
        )
        .distinct()
    )
    pending.createOrReplaceTempView("__sublineage_pending")
    pending_count = pending.count()
    logger.info("pyspark snippets needing translation pipeline_run_id=%s n=%d",
                pipeline_run_id, pending_count)

    if pending_count == 0:
        return {"pending": 0, "translated": 0, "backfilled": 0}

    # New snippets only - left-anti against the cache
    new_count = spark.sql(
        f"""
        SELECT COUNT(*) AS n
        FROM __sublineage_pending p
        LEFT ANTI JOIN {cache_table} c ON p.expr_sha = c.expr_sha
        """
    ).collect()[0]["n"]
    logger.info("new translations to request n=%d", new_count)

    if new_count > 0:
        # Escape the prompt for SQL string interpolation
        safe_prompt = _PROMPT.replace("'", "''")
        safe_model = model.replace("'", "''")
        # GROUP BY expr_sha on the source guarantees one row per hash in the
        # INSERT, even if the same snippet appears in __sublineage_pending
        # twice with hash collisions or normalization noise.
        spark.sql(
            f"""
            INSERT INTO {cache_table}
            SELECT
              expr_sha,
              ANY_VALUE(pyspark_expr)                 AS pyspark_expr,
              ai_query(
                '{safe_model}',
                CONCAT('{safe_prompt}', ANY_VALUE(pyspark_expr))
              )                                       AS sql_expr,
              '{safe_model}'                          AS model,
              current_timestamp()                     AS translated_at
            FROM (
              SELECT p.expr_sha, p.pyspark_expr
              FROM __sublineage_pending p
              LEFT ANTI JOIN {cache_table} c ON p.expr_sha = c.expr_sha
            )
            GROUP BY expr_sha
            """
        )

    # Dedupe the cache side of the MERGE: prior runs may have inserted
    # duplicate expr_sha rows (no UNIQUE constraint on the cache table).
    # Without this collapse, multiple source rows can match a single target
    # row, which Delta MERGE rejects with
    # DELTA_MULTIPLE_SOURCE_ROW_MATCHING_TARGET_ROW_IN_MERGE.
    backfill_result = spark.sql(
        f"""
        MERGE INTO {endpoints_table} AS t
        USING (
          SELECT
            expr_sha,
            ANY_VALUE(sql_expr) AS sql_expr
          FROM {cache_table}
          WHERE sql_expr IS NOT NULL
          GROUP BY expr_sha
        ) AS c
          ON sha2(t.expr, 256) = c.expr_sha
         AND t.pipeline_run_id = '{safe_run_id}'
         AND t.expr_lang        = 'pyspark'
         AND t.expr_sql IS NULL
        WHEN MATCHED THEN UPDATE SET t.expr_sql = c.sql_expr
        """
    ).collect()
    backfilled = (
        int(backfill_result[0]["num_updated_rows"])
        if backfill_result and "num_updated_rows" in backfill_result[0].asDict()
        else 0
    )

    logger.info(
        "expression enrichment complete pipeline_run_id=%s pending=%d translated=%d backfilled=%d",
        pipeline_run_id,
        pending_count,
        new_count,
        backfilled,
    )
    return {"pending": pending_count, "translated": new_count, "backfilled": backfilled}
