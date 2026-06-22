"""In-memory adjacency for sublineage path queries.

Loads column-to-column hops from `lineage_edge_endpoints` into driver-side
dicts keyed by node_id. BFS/DFS modules walk this structure — they never
touch Spark during traversal, keeping path queries under sub-second latency
once the graph is loaded.

The graph stores both forward (src → dst, "downstream" walks) and reverse
(dst → src, "upstream" walks) adjacency. Same HopRecord objects are shared
across both indexes so memory is proportional to the edge count, not 2×.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pyspark.sql import SparkSession
from pyspark.sql import functions as F


Direction = Literal["downstream", "upstream"]

# Validates that a string is a safe SQL identifier or UUID (no injection risk)
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _validate_id(value: str, name: str) -> str:
    """Validate that a value is safe for SQL interpolation (alphanumeric + hyphens + underscores)."""
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {name}: contains unsafe characters: {value!r}")
    return value


@dataclass(frozen=True)
class HopRecord:
    """One column-to-column derivation, as stored in lineage_edge_endpoints."""

    src_node_id: str
    dst_node_id: str
    edge_id: str | None
    artifact_id: str | None
    source_path: str | None
    expr: str | None
    expr_lang: str | None
    expr_sql: str | None
    transform_category: str | None
    src_fqn: str | None
    src_col: str | None
    dst_fqn: str | None
    dst_col: str | None


@dataclass
class SublineageGraph:
    """Driver-side adjacency view over `lineage_edge_endpoints`."""

    pipeline_run_id: str
    out_adj: dict[str, list[HopRecord]] = field(default_factory=dict)
    in_adj: dict[str, list[HopRecord]] = field(default_factory=dict)
    nodes: set[str] = field(default_factory=set)

    def neighbors(self, node_id: str, direction: Direction) -> list[HopRecord]:
        """Return hops adjacent to `node_id` in the given walk direction."""
        if direction == "downstream":
            return self.out_adj.get(node_id, [])
        if direction == "upstream":
            return self.in_adj.get(node_id, [])
        raise ValueError(f"unknown direction: {direction!r}")

    def has_node(self, node_id: str) -> bool:
        return node_id in self.nodes

    def edge_count(self) -> int:
        return sum(len(v) for v in self.out_adj.values())


def load_graph(
    spark: SparkSession,
    *,
    pipeline_run_id: str,
    endpoints_table: str,
) -> SublineageGraph:
    """Load the adjacency list for one pipeline run into memory."""
    _validate_id(pipeline_run_id, "pipeline_run_id")
    rows = (
        spark.table(endpoints_table)
        .where(F.col("pipeline_run_id") == pipeline_run_id)
        .select(
            "src_node_id", "dst_node_id", "edge_id", "artifact_id", "source_path",
            "expr", "expr_lang", "expr_sql", "transform_category",
            "src_fqn", "src_col", "dst_fqn", "dst_col"
        )
        .collect()
    )
    return _build_graph(pipeline_run_id, rows)


def load_graph_latest(
    spark: SparkSession,
    *,
    endpoints_table: str,
) -> SublineageGraph:
    """Load the adjacency list for the most recent pipeline run."""
    latest = (
        spark.table(endpoints_table)
        .orderBy(F.col("materialized_at").desc())
        .select("pipeline_run_id")
        .limit(1)
        .collect()
    )
    if not latest:
        return SublineageGraph(pipeline_run_id="")
    return load_graph(
        spark, pipeline_run_id=latest[0]["pipeline_run_id"], endpoints_table=endpoints_table
    )


def _build_graph(pipeline_run_id: str, rows) -> SublineageGraph:
    g = SublineageGraph(pipeline_run_id=pipeline_run_id)
    for r in rows:
        cols = r.asDict() if hasattr(r, "asDict") else {}
        hop = HopRecord(
            src_node_id=r["src_node_id"],
            dst_node_id=r["dst_node_id"],
            edge_id=r["edge_id"],
            artifact_id=r["artifact_id"],
            source_path=r["source_path"],
            expr=r["expr"],
            expr_lang=cols.get("expr_lang"),
            expr_sql=cols.get("expr_sql"),
            transform_category=r["transform_category"],
            src_fqn=r["src_fqn"],
            src_col=r["src_col"],
            dst_fqn=r["dst_fqn"],
            dst_col=r["dst_col"],
        )
        g.out_adj.setdefault(hop.src_node_id, []).append(hop)
        g.in_adj.setdefault(hop.dst_node_id, []).append(hop)
        g.nodes.add(hop.src_node_id)
        g.nodes.add(hop.dst_node_id)
    return g
