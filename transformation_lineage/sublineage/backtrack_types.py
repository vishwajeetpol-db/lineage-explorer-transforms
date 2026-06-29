"""Data structures for level-organized backward traversal (backtracking).

Supports multiple transformations and multiple source columns per level,
capturing the full fan-in/fan-out of column-level lineage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BacktrackNode:
    """A column node in the backtracking tree."""
    node_id: str
    table_fqn: str | None
    column_name: str | None
    depth: int = 0
    is_target: bool = False
    is_source: bool = False  # True if no upstream edges (leaf node)

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "table_fqn": self.table_fqn,
            "column_name": self.column_name,
            "depth": self.depth,
            "is_target": self.is_target,
            "is_source": self.is_source,
        }


@dataclass
class BacktrackEdge:
    """A transformation edge connecting two column nodes."""
    src_node_id: str
    dst_node_id: str
    edge_id: str | None
    artifact_id: str | None
    source_path: str | None  # notebook path
    expr: str | None
    transform_category: str | None
    src_fqn: str | None
    src_col: str | None
    dst_fqn: str | None
    dst_col: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "src_node_id": self.src_node_id,
            "dst_node_id": self.dst_node_id,
            "edge_id": self.edge_id,
            "artifact_id": self.artifact_id,
            "source_path": self.source_path,
            "expr": self.expr,
            "transform_category": self.transform_category,
            "src_fqn": self.src_fqn,
            "src_col": self.src_col,
            "dst_fqn": self.dst_fqn,
            "dst_col": self.dst_col,
        }


@dataclass
class BacktrackLevel:
    """One depth tier in the backtracking tree.
    
    Can contain MULTIPLE columns (from different tables) and
    MULTIPLE transformation edges (from different notebooks/artifacts).
    """
    depth: int
    columns: list[BacktrackNode] = field(default_factory=list)
    transformations: list[BacktrackEdge] = field(default_factory=list)

    @property
    def column_count(self) -> int:
        return len(self.columns)

    @property
    def transformation_count(self) -> int:
        return len(self.transformations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "depth": self.depth,
            "columns": [c.to_dict() for c in self.columns],
            "transformations": [t.to_dict() for t in self.transformations],
            "column_count": self.column_count,
            "transformation_count": self.transformation_count,
        }


@dataclass
class BacktrackResult:
    """Complete backtracking result from a target column."""
    target_node_id: str
    target_fqn: str | None
    target_column: str | None
    levels: list[BacktrackLevel] = field(default_factory=list)
    total_nodes: int = 0
    total_edges: int = 0
    max_depth_reached: int = 0
    pipeline_run_id: str | None = None
    version_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_node_id": self.target_node_id,
            "target_fqn": self.target_fqn,
            "target_column": self.target_column,
            "levels": [lvl.to_dict() for lvl in self.levels],
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "max_depth_reached": self.max_depth_reached,
            "pipeline_run_id": self.pipeline_run_id,
            "version_hash": self.version_hash,
        }
