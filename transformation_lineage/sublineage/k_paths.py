"""Depth-limited DFS for all / K shortest column-to-column paths.

Enumerates simple paths (no repeated nodes) from `src` to `dst` up to
`max_depth` hops and `max_paths` total paths. Returns paths sorted by length
so the top-K shortest fall at the front of the result.

Semantics
---------
- Simple paths only: a node appears at most once per path, preventing cycles.
- Depth cap bounds worst-case cost in dense graphs; callers should raise it
  only when they know the subgraph stays small.
- `max_paths` is a hard stop on total enumeration — useful because unbounded
  DFS on richly connected KPI subgraphs can explode exponentially.

A future Yen's algorithm can replace this for weighted K-shortest queries;
the API signature stays the same so callers don't change.
"""

from __future__ import annotations

from transformation_lineage.sublineage.graph import HopRecord, SublineageGraph


def find_paths(
    graph: SublineageGraph,
    src: str,
    dst: str,
    *,
    max_depth: int = 6,
    max_paths: int = 25,
) -> list[list[HopRecord]]:
    if src == dst:
        return [[]]
    if not graph.has_node(src) or not graph.has_node(dst):
        return []
    if max_paths <= 0 or max_depth <= 0:
        return []

    results: list[list[HopRecord]] = []
    path: list[HopRecord] = []
    visited: set[str] = {src}

    def _dfs(node: str, depth: int) -> None:
        if len(results) >= max_paths:
            return
        if depth >= max_depth:
            return
        for hop in graph.neighbors(node, "downstream"):
            nxt = hop.dst_node_id
            if nxt in visited:
                continue
            path.append(hop)
            if nxt == dst:
                results.append(list(path))
                path.pop()
                if len(results) >= max_paths:
                    return
                continue
            visited.add(nxt)
            _dfs(nxt, depth + 1)
            visited.remove(nxt)
            path.pop()
            if len(results) >= max_paths:
                return

    _dfs(src, 0)
    results.sort(key=len)
    return results
