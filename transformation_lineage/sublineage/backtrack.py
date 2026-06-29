"""Backward traversal (backtracking) from a target column through all upstream levels.

BFS-based algorithm that walks upstream from a target column, collecting
ALL transformation edges at each depth level. Supports multiple transformations
and multiple source columns per level (full fan-in capture).

Optimizations applied:
    1. Visited set (memoization): prevents re-traversal of nodes already
       discovered at a shallower level. Edges to already-visited nodes are
       still captured for complete fan-in representation.
    2. Pruning: optional table-pattern and transform-category filters let
       callers skip staging/tmp tables or irrelevant transform types without
       post-processing. Defaults to no pruning (safe, backward compatible).
    3. Adjacency lists: SublineageGraph uses dict[str, list[HopRecord]] giving
       O(1) neighbor lookups. No adjacency matrix overhead.
    4. Edge deduplication: seen_edges set within each level prevents duplicate
       edges when multiple frontier nodes share the same upstream hop.
    5. Compiled regex: node ID parsing regex compiled once at module level.

Usage:
    from transformation_lineage.sublineage.backtrack import backtrack_from_target

    # Basic (no pruning)
    result = backtrack_from_target(graph, "col:catalog.schema.table::column")

    # With pruning: skip staging tables, only show aggregation/arithmetic transforms
    result = backtrack_from_target(
        graph,
        "col:catalog.schema.table::column",
        exclude_table_patterns=["staging", "tmp", "raw"],
        include_categories={"aggregation", "arithmetic", "window"},
    )
"""

from __future__ import annotations

import re
from typing import Sequence, Set, Tuple

from transformation_lineage.sublineage.backtrack_types import (
    BacktrackEdge,
    BacktrackLevel,
    BacktrackNode,
    BacktrackResult,
)
from transformation_lineage.sublineage.graph import HopRecord, SublineageGraph

# Compiled once at module level — avoids re-compilation per call
_COL_NODE_RE = re.compile(r"^col:([^:]+)::(.+)$")


def _parse_node_id(node_id: str) -> Tuple[str | None, str | None]:
    """Parse 'col:catalog.schema.table::column' into (fqn, column_name).

    Uses module-level compiled regex for O(1) repeated calls.
    Returns (None, None) if format doesn't match.
    """
    match = _COL_NODE_RE.match(node_id)
    if match:
        return match.group(1), match.group(2)
    # Fallback for non-canonical patterns like col:out:artifact:column
    parts = node_id.split(":")
    if len(parts) >= 2 and parts[0] == "col":
        return None, parts[-1] if len(parts) > 1 else None
    return None, None


def _hop_to_edge(hop: HopRecord) -> BacktrackEdge:
    """Convert a HopRecord to a BacktrackEdge."""
    return BacktrackEdge(
        src_node_id=hop.src_node_id,
        dst_node_id=hop.dst_node_id,
        edge_id=hop.edge_id,
        artifact_id=hop.artifact_id,
        source_path=hop.source_path,
        expr=hop.expr,
        transform_category=hop.transform_category,
        src_fqn=hop.src_fqn,
        src_col=hop.src_col,
        dst_fqn=hop.dst_fqn,
        dst_col=hop.dst_col,
    )


def _compile_table_patterns(
    patterns: Sequence[str] | None,
) -> list[re.Pattern[str]] | None:
    """Compile table exclusion patterns once before traversal."""
    if not patterns:
        return None
    return [re.compile(p, re.IGNORECASE) for p in patterns]


def _should_prune_hop(
    hop: HopRecord,
    *,
    compiled_patterns: list[re.Pattern[str]] | None,
    include_categories: Set[str] | None,
) -> bool:
    """Return True if this hop should be skipped based on pruning rules.

    Pruning is conservative: a hop is only skipped if it matches an
    explicit exclusion. When no filters are set, nothing is pruned.
    """
    # Category filter: skip edges whose transform_category is not in the
    # include set. If include_categories is None, all categories pass.
    if include_categories is not None:
        cat = (hop.transform_category or "unknown").lower()
        if cat not in include_categories:
            return True

    # Table pattern filter: skip edges whose SOURCE table FQN matches
    # any exclusion pattern. This drops hops from staging/tmp tables
    # without affecting the target or intermediate nodes that don't
    # match the pattern.
    if compiled_patterns is not None and hop.src_fqn:
        fqn_lower = hop.src_fqn.lower()
        for pat in compiled_patterns:
            if pat.search(fqn_lower):
                return True

    return False


def backtrack_from_target(
    graph: SublineageGraph,
    target_node_id: str,
    max_depth: int = 6,
    *,
    exclude_table_patterns: Sequence[str] | None = None,
    include_categories: Set[str] | None = None,
) -> BacktrackResult | None:
    """
    Walk backward (upstream) from a target column, collecting all transformations
    at each depth level.

    Parameters
    ----------
    graph : SublineageGraph
        In-memory adjacency loaded from lineage_edge_endpoints.
    target_node_id : str
        Canonical column node ID, e.g. "col:catalog.schema.table::column".
    max_depth : int
        Maximum levels to traverse upstream.
    exclude_table_patterns : list of str, optional
        Regex patterns matched against source table FQNs (case-insensitive).
        Hops from matching tables are pruned. Useful for skipping staging,
        tmp, or raw tables.  Examples: ["staging", "tmp_", ".*\\.raw\\..*"]
    include_categories : set of str, optional
        If provided, only edges whose transform_category is in this set are
        kept. Others are pruned.  Examples: {"aggregation", "arithmetic"}
        When None (default), all categories are included.

    Returns
    -------
    BacktrackResult or None if target not found in graph.

    Optimizations
    -------------
    - Visited set: nodes appear at their shallowest BFS level only.
    - Edge dedup: seen_edges set per level prevents duplicate edges.
    - Pruning: exclude_table_patterns and include_categories skip irrelevant
      hops DURING traversal (not post-hoc), saving work at every level.
    - Adjacency lists: graph.neighbors() is O(1) dict lookup.
    - Compiled regex: _parse_node_id uses module-level compiled pattern;
      table patterns are compiled once before the BFS loop.
    """
    if not graph.has_node(target_node_id):
        return None

    target_fqn, target_col = _parse_node_id(target_node_id)

    # Compile exclusion patterns once before the loop
    compiled_patterns = _compile_table_patterns(exclude_table_patterns)
    # Normalize category filter to lowercase for case-insensitive matching
    norm_categories: Set[str] | None = None
    if include_categories is not None:
        norm_categories = {c.lower() for c in include_categories}

    # Initialize result
    levels: list[BacktrackLevel] = []
    all_nodes: dict[str, BacktrackNode] = {}
    all_edges: list[BacktrackEdge] = []
    visited: set[str] = set()

    # Level 0: Target node
    target_node = BacktrackNode(
        node_id=target_node_id,
        table_fqn=target_fqn,
        column_name=target_col,
        depth=0,
        is_target=True,
        is_source=False,
    )
    all_nodes[target_node_id] = target_node
    visited.add(target_node_id)

    level_0 = BacktrackLevel(depth=0, columns=[target_node], transformations=[])
    levels.append(level_0)

    # BFS: current frontier = nodes at current depth
    current_frontier: set[str] = {target_node_id}

    for depth in range(1, max_depth + 1):
        if not current_frontier:
            break

        level_columns: list[BacktrackNode] = []
        level_transformations: list[BacktrackEdge] = []
        next_frontier: set[str] = set()
        seen_edges: set[str] = set()  # Dedup edges within this level

        # For each node in current frontier, get ALL upstream hops — O(1) each
        for node_id in current_frontier:
            upstream_hops = graph.neighbors(node_id, "upstream")

            for hop in upstream_hops:
                # --- Pruning: skip hops that match exclusion filters ---
                if _should_prune_hop(
                    hop,
                    compiled_patterns=compiled_patterns,
                    include_categories=norm_categories,
                ):
                    continue

                # --- Edge deduplication within this level ---
                edge_key = f"{hop.src_node_id}|{hop.dst_node_id}|{hop.edge_id}"
                if edge_key not in seen_edges:
                    edge = _hop_to_edge(hop)
                    level_transformations.append(edge)
                    all_edges.append(edge)
                    seen_edges.add(edge_key)

                # --- Visited set: add source node only once (shallowest level) ---
                src_id = hop.src_node_id
                if src_id not in visited:
                    visited.add(src_id)
                    src_fqn, src_col = _parse_node_id(src_id)
                    # Use hop's metadata if available (more accurate than parsing)
                    if hop.src_fqn:
                        src_fqn = hop.src_fqn
                    if hop.src_col:
                        src_col = hop.src_col

                    src_node = BacktrackNode(
                        node_id=src_id,
                        table_fqn=src_fqn,
                        column_name=src_col,
                        depth=depth,
                        is_target=False,
                        is_source=False,  # Determined after traversal
                    )
                    level_columns.append(src_node)
                    all_nodes[src_id] = src_node
                    next_frontier.add(src_id)

        if level_columns or level_transformations:
            level = BacktrackLevel(
                depth=depth,
                columns=level_columns,
                transformations=level_transformations,
            )
            levels.append(level)

        current_frontier = next_frontier

    # Mark source nodes (those with no upstream edges in graph)
    for node_id, node in all_nodes.items():
        if not node.is_target:
            upstream = graph.neighbors(node_id, "upstream")
            if not upstream:
                node.is_source = True

    max_depth_reached = max((lvl.depth for lvl in levels), default=0)

    return BacktrackResult(
        target_node_id=target_node_id,
        target_fqn=target_fqn,
        target_column=target_col,
        levels=levels,
        total_nodes=len(all_nodes),
        total_edges=len(all_edges),
        max_depth_reached=max_depth_reached,
        pipeline_run_id=graph.pipeline_run_id,
        version_hash=None,  # Set by API layer
    )
