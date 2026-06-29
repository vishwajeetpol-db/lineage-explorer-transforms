"""Public sub-lineage entrypoints.

Cost optimizations:
  - Session-level graph caching: graph is loaded ONCE per (run_id, endpoints_table)
    and reused across find_paths(), find_shortest_path(), and backtrack_target().
    In a typical notebook session this saves 1-2 full table scans.
  - Delta cache integration: path results stored in lineage_sublineage_cache.
  - Version-hash gating: computation skipped when graph hasn't changed.

Usage (Databricks notebook)::

    from transformation_lineage.sublineage.api import find_shortest_path, backtrack_target

    result = find_shortest_path(
        spark, cfg,
        src="col:supply_chain_bronze.raw.orders::quantity",
        dst="col:supply_chain_gold.demand_forecast::forecast_qty",
    )

    result = backtrack_target(
        spark, cfg,
        target="col:supply_chain_gold.demand_forecast::forecast_qty",
    )
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Sequence, Set

from pyspark.sql import SparkSession

from transformation_lineage.config import LineageJobConfig
from transformation_lineage.sublineage.backtrack import backtrack_from_target
from transformation_lineage.sublineage.bfs import shortest_path
from transformation_lineage.sublineage.cache_keys import (
    compute_cache_key,
    compute_version_hash,
    read_cache,
    write_cache,
)
from transformation_lineage.sublineage.graph import HopRecord, SublineageGraph, load_graph
from transformation_lineage.sublineage.k_paths import find_paths as _dfs_find_paths

logger = logging.getLogger(__name__)

Mode = Literal["shortest", "all", "k"]

# ── Session-level graph cache ─────────────────────────────────────────
# Avoids reloading the same graph when running both find_paths() and
# backtrack_target() in the same notebook session (saves 1 full table scan).
_GRAPH_CACHE: dict[str, SublineageGraph] = {}  # key = f"{run_id}|{endpoints_table}"
_VERSION_CACHE: dict[str, str] = {}  # key = f"{run_id}|{endpoints_table}" -> version_hash


def _cache_key_for(run_id: str, endpoints_table: str) -> str:
    return f"{run_id}|{endpoints_table}"


def _get_or_load_graph(
    spark: SparkSession, run_id: str, endpoints_table: str
) -> SublineageGraph:
    """Return cached graph or load from Delta (1 query per session per run_id)."""
    ck = _cache_key_for(run_id, endpoints_table)
    if ck not in _GRAPH_CACHE:
        logger.info("Loading graph for run_id=%s (will be cached for session)", run_id)
        _GRAPH_CACHE[ck] = load_graph(spark, pipeline_run_id=run_id, endpoints_table=endpoints_table)
    else:
        logger.info("Reusing cached graph for run_id=%s (saved 1 table scan)", run_id)
    return _GRAPH_CACHE[ck]


def _get_or_compute_version_hash(
    spark: SparkSession, run_id: str, endpoints_table: str
) -> str:
    """Return cached version hash or compute (1 query per session per run_id)."""
    ck = _cache_key_for(run_id, endpoints_table)
    if ck not in _VERSION_CACHE:
        _VERSION_CACHE[ck] = compute_version_hash(
            spark, endpoints_table=endpoints_table, pipeline_run_id=run_id
        )
    return _VERSION_CACHE[ck]


def invalidate_graph_cache() -> None:
    """Clear the session graph cache (call after a new pipeline run)."""
    _GRAPH_CACHE.clear()
    _VERSION_CACHE.clear()


def _resolve_run_id(
    spark: SparkSession, endpoints_table: str, pipeline_run_id: str
) -> str:
    if pipeline_run_id != "latest":
        return pipeline_run_id
    from pyspark.sql import functions as F
    row = (
        spark.table(endpoints_table)
        .orderBy(F.col("materialized_at").desc())
        .select("pipeline_run_id")
        .limit(1)
        .collect()
    )
    if not row:
        raise ValueError(
            f"No rows in {endpoints_table}. Run the daily pipeline first so "
            "edge_endpoints is populated."
        )
    return row[0]["pipeline_run_id"]


def _hop_to_edge_dict(hop: HopRecord) -> dict[str, Any]:
    return {
        "edge_type": "derive",
        "edge_id": hop.edge_id,
        "artifact_id": hop.artifact_id,
        "notebook_path": hop.source_path,
        "transform_category": hop.transform_category,
        "expr": hop.expr_sql or hop.expr,
        "expr_lang": hop.expr_lang,
        "expr_raw": hop.expr,
    }


def _node_dict(node_id: str, fqn: str | None, column: str | None) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "node_type": "column",
        "table_fqn": fqn,
        "column_name": column,
    }


def _render_path(path: list[HopRecord], src: str) -> list[dict[str, Any]]:
    if not path:
        return []
    rendered: list[dict[str, Any]] = [
        {"step": 1, "node": _node_dict(src, path[0].src_fqn, path[0].src_col)}
    ]
    for i, hop in enumerate(path, start=2):
        rendered.append({
            "step": i,
            "edge": _hop_to_edge_dict(hop),
            "node": _node_dict(hop.dst_node_id, hop.dst_fqn, hop.dst_col),
        })
    return rendered


def _run_algorithm(
    graph: SublineageGraph, src: str, dst: str,
    mode: Mode, max_depth: int, max_paths: int,
) -> list[list[HopRecord]]:
    if mode == "shortest":
        p = shortest_path(graph, src, dst, max_depth=max_depth)
        return [p] if p is not None else []
    if mode in ("all", "k"):
        return _dfs_find_paths(graph, src, dst, max_depth=max_depth, max_paths=max_paths)
    raise ValueError(f"unknown mode: {mode!r}")


def find_paths(
    spark: SparkSession,
    cfg: LineageJobConfig,
    *,
    src: str,
    dst: str,
    mode: Mode = "shortest",
    pipeline_run_id: str = "latest",
    max_depth: int = 6,
    max_paths: int = 25,
    use_cache: bool = True,
) -> dict[str, Any]:
    """Find source→target column paths. Uses session graph cache."""
    endpoints_table = cfg.fully_qualified("lineage_edge_endpoints")
    cache_table = cfg.fully_qualified("lineage_sublineage_cache")

    run_id = _resolve_run_id(spark, endpoints_table, pipeline_run_id)
    version_hash = _get_or_compute_version_hash(spark, run_id, endpoints_table)
    cache_key = compute_cache_key(
        src_node_id=src, dst_node_id=dst, mode=mode,
        max_depth=max_depth, max_paths=max_paths, version_hash=version_hash,
    )

    if use_cache:
        hit = read_cache(spark, cache_table=cache_table, cache_key=cache_key)
        if hit is not None:
            logger.info("sublineage cache hit key=%s", cache_key[:12])
            hit["pipeline_run_id"] = run_id
            return hit

    graph = _get_or_load_graph(spark, run_id, endpoints_table)
    raw_paths = _run_algorithm(graph, src, dst, mode, max_depth, max_paths)
    rendered = [_render_path(p, src) for p in raw_paths]
    hop_count = min((len(p) for p in raw_paths), default=0)

    result: dict[str, Any] = {
        "src_node_id": src, "dst_node_id": dst, "mode": mode,
        "hop_count": hop_count, "path_count": len(raw_paths),
        "version_hash": version_hash, "paths": rendered,
        "pipeline_run_id": run_id, "cache_hit": False,
    }

    if use_cache and raw_paths:
        write_cache(
            spark, cache_table=cache_table, pipeline_run_id=run_id,
            cache_key=cache_key, src_node_id=src, dst_node_id=dst,
            mode=mode, hop_count=hop_count, path_count=len(raw_paths),
            paths_json=json.dumps(rendered), version_hash=version_hash,
        )

    return result


def find_shortest_path(
    spark: SparkSession, cfg: LineageJobConfig, *,
    src: str, dst: str, pipeline_run_id: str = "latest",
    max_depth: int = 6, use_cache: bool = True,
) -> dict[str, Any]:
    """Convenience wrapper for find_paths(mode="shortest")."""
    return find_paths(
        spark, cfg, src=src, dst=dst, mode="shortest",
        pipeline_run_id=pipeline_run_id, max_depth=max_depth, use_cache=use_cache,
    )


def backtrack_target(
    spark: SparkSession,
    cfg: LineageJobConfig,
    *,
    target: str,
    pipeline_run_id: str = "latest",
    max_depth: int = 6,
    exclude_table_patterns: Sequence[str] | None = None,
    include_categories: Set[str] | None = None,
) -> dict[str, Any]:
    """Backtrack from target column through all upstream levels.
    
    Reuses the session graph cache — if find_paths() was called first with
    the same run_id, the graph is already loaded (zero additional I/O).
    """
    endpoints_table = cfg.fully_qualified("lineage_edge_endpoints")

    try:
        run_id = _resolve_run_id(spark, endpoints_table, pipeline_run_id)
    except ValueError as e:
        return {"error": str(e)}

    version_hash = _get_or_compute_version_hash(spark, run_id, endpoints_table)
    graph = _get_or_load_graph(spark, run_id, endpoints_table)

    result = backtrack_from_target(
        graph, target, max_depth=max_depth,
        exclude_table_patterns=exclude_table_patterns,
        include_categories=include_categories,
    )

    if result is None:
        return {
            "error": f"Target node not found in graph: {target}",
            "pipeline_run_id": run_id,
            "available_nodes_sample": list(graph.nodes)[:20],
        }

    result.pipeline_run_id = run_id
    result.version_hash = version_hash
    return result.to_dict()
