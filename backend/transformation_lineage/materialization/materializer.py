"""Pre-compute serialized lineage subgraphs per KPI table (no sub-lineage path engine)."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any

from pyspark.sql import Row, SparkSession
from pyspark.sql import functions as F

from transformation_lineage.config import LineageJobConfig


def _collect_limited(df, limit: int = 50_000) -> list[dict[str, Any]]:
    return [r.asDict() for r in df.limit(limit).collect()]


def _expand_subgraph(
    node_rows: list[dict[str, Any]],
    edge_rows: list[dict[str, Any]],
    seed_ids: set[str],
    max_iterations: int = 6,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """BFS expansion from seed nodes using an adjacency index.

    Uses pre-built adjacency lists for O(nodes + edges) traversal
    instead of scanning all edges per iteration.
    """
    # Build adjacency index once: node_id -> list of (edge, neighbor)
    adj: dict[str, list[tuple[dict[str, Any], str]]] = defaultdict(list)
    for e in edge_rows:
        adj[e["src_id"]].append((e, e["dst_id"]))
        adj[e["dst_id"]].append((e, e["src_id"]))

    active = set(seed_ids)
    used_edge_ids: set[str] = set()
    used_edges: list[dict[str, Any]] = []

    # BFS with depth limit
    frontier = deque(seed_ids)
    visited = set(seed_ids)
    depth = 0

    while frontier and depth < max_iterations:
        next_frontier: deque[str] = deque()
        for _ in range(len(frontier)):
            node = frontier.popleft()
            for edge, neighbor in adj.get(node, []):
                eid = edge["edge_id"]
                if eid not in used_edge_ids:
                    used_edges.append(edge)
                    used_edge_ids.add(eid)
                if neighbor not in visited:
                    visited.add(neighbor)
                    active.add(neighbor)
                    next_frontier.append(neighbor)
        frontier = next_frontier
        depth += 1

    node_subset = [n for n in node_rows if n["node_id"] in active]
    return node_subset, used_edges


def materialize_kpi_subgraphs(
    spark: SparkSession,
    cfg: LineageJobConfig,
    *,
    pipeline_run_id: str,
    nodes_table: str,
    edges_table: str,
    cache_table: str,
) -> None:
    """
    For each KPI FQN, build a bounded subgraph around `tbl:{kpi}` and cache JSON in Delta.

    This satisfies PRD graph materialization for KPI-scoped views without implementing
    arbitrary source→target sub-lineage queries (see SUBLINEAGE_FRAMEWORK.md).
    """
    nodes_df = spark.table(nodes_table).where(F.col("pipeline_run_id") == pipeline_run_id)
    edges_df = spark.table(edges_table).where(F.col("pipeline_run_id") == pipeline_run_id)
    node_rows = _collect_limited(nodes_df)
    edge_rows = _collect_limited(edges_df)

    now = datetime.now(timezone.utc)
    out_rows: list[Row] = []

    for kpi in cfg.kpi_tables:
        seed = {f"tbl:{kpi}"}
        nsub, esub = _expand_subgraph(node_rows, edge_rows, seed)
        payload = {"nodes": nsub, "edges": esub}
        raw = json.dumps(payload, sort_keys=True, default=str)
        vhash = hashlib.sha256(raw.encode()).hexdigest()[:32]
        cache_key = f"kpi_full_upstream:{kpi}:sha:{vhash}"
        out_rows.append(
            Row(
                pipeline_run_id=pipeline_run_id,
                cache_key=cache_key,
                kpi_table_fqn=kpi,
                graph_json=raw,
                version_hash=vhash,
                materialized_at=now,
            )
        )

    if out_rows:
        spark.createDataFrame(out_rows).write.format("delta").mode("append").saveAsTable(cache_table)
