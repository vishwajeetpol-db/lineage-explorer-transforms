"""Optional reconciliation between stored parsed lineage and UC system column lineage."""

from __future__ import annotations

from datetime import datetime, timezone

from pyspark.sql import Row, SparkSession
from pyspark.sql import functions as F


def load_system_column_lineage_sample(
    spark: SparkSession,
    *,
    kpi_tables: list[str],
    lookback_hours: int,
):
    """Read recent system lineage rows scoped to KPI tables."""
    if not kpi_tables:
        return spark.sql("SELECT CAST(NULL AS STRING) AS source_table_full_name WHERE 1=0")
    conds = []
    for t in kpi_tables:
        safe = t.replace("'", "''")
        conds.append(f"source_table_full_name = '{safe}'")
        conds.append(f"target_table_full_name = '{safe}'")
    where = " OR ".join(conds)
    sql = f"""
    SELECT
      source_table_full_name,
      target_table_full_name,
      source_column_name,
      target_column_name,
      entity_type,
      entity_run_id,
      event_time
    FROM system.access.column_lineage
    WHERE event_time >= current_timestamp() - INTERVAL {int(lookback_hours)} HOURS
      AND ({where})
    """
    return spark.sql(sql)


def write_reconciliation_stats(
    spark: SparkSession,
    *,
    pipeline_run_id: str,
    target_edges_table: str,
    kpi_tables: list[str],
    lookback_hours: int,
    output_table: str,
) -> None:
    """
    Persist a single-row summary comparing system lineage volume vs stored parsed edges.

    Extend this function to emit pair-level discrepancies once edge endpoints carry FQN columns.
    """
    sys_df = load_system_column_lineage_sample(spark, kpi_tables=kpi_tables, lookback_hours=lookback_hours)
    sys_cnt = sys_df.count()

    edges = spark.table(target_edges_table)
    parsed_cnt = edges.where(F.col("pipeline_run_id") == pipeline_run_id).count()

    row = Row(
        pipeline_run_id=pipeline_run_id,
        system_lineage_row_count=sys_cnt,
        parsed_edge_count_run=parsed_cnt,
        note="pair-level diff: join edges to nodes with FQN labels (see SUBLINEAGE_FRAMEWORK.md)",
        recorded_at=datetime.now(timezone.utc),
    )
    spark.createDataFrame([row]).write.format("delta").mode("append").saveAsTable(output_table)
