"""Bidirectional BFS for shortest column-to-column path.

Expands two frontiers: forward from `src` using downstream adjacency, and
backward from `dst` using upstream adjacency. The smaller frontier is
expanded each step. When the frontiers intersect, the meeting node is used
to reconstruct a path by walking parent pointers from both sides.

Returns the path as an ordered list of `HopRecord` objects such that
`path[0].src_node_id == src` and `path[-1].dst_node_id == dst`, with
`path[i].dst_node_id == path[i+1].src_node_id` for adjacent hops. Returns
`None` when no path exists within `max_depth`.

Correctness: levels are processed in full before reconstruction, so the
first meeting point produces a provably shortest path (ties broken
arbitrarily; Task 7 enumerates K shortest paths).
"""

from __future__ import annotations

from transformation_lineage.sublineage.graph import HopRecord, SublineageGraph


def shortest_path(
    graph: SublineageGraph,
    src: str,
    dst: str,
    *,
    max_depth: int = 6,
) -> list[HopRecord] | None:
    if src == dst:
        return []
    if not graph.has_node(src) or not graph.has_node(dst):
        return None

    # parent[node] = (parent_node, hop_joining_parent_to_node_in_natural_edge_dir)
    fwd: dict[str, tuple[str | None, HopRecord | None]] = {src: (None, None)}
    bwd: dict[str, tuple[str | None, HopRecord | None]] = {dst: (None, None)}

    fwd_frontier: set[str] = {src}
    bwd_frontier: set[str] = {dst}
    depth_fwd = 0
    depth_bwd = 0

    meeting: str | None = None

    while fwd_frontier and bwd_frontier:
        if depth_fwd + depth_bwd >= max_depth:
            break

        if len(fwd_frontier) <= len(bwd_frontier):
            next_frontier: set[str] = set()
            for node in fwd_frontier:
                for hop in graph.neighbors(node, "downstream"):
                    nxt = hop.dst_node_id
                    if nxt in fwd:
                        continue
                    fwd[nxt] = (node, hop)
                    next_frontier.add(nxt)
                    if nxt in bwd and meeting is None:
                        meeting = nxt
            fwd_frontier = next_frontier
            depth_fwd += 1
        else:
            next_frontier = set()
            for node in bwd_frontier:
                for hop in graph.neighbors(node, "upstream"):
                    nxt = hop.src_node_id
                    if nxt in bwd:
                        continue
                    # hop goes nxt -> node in natural edge direction
                    bwd[nxt] = (node, hop)
                    next_frontier.add(nxt)
                    if nxt in fwd and meeting is None:
                        meeting = nxt
            bwd_frontier = next_frontier
            depth_bwd += 1

        if meeting is not None:
            break

    if meeting is None:
        return None

    fwd_path: list[HopRecord] = []
    cur = meeting
    while fwd[cur][0] is not None:
        parent, hop = fwd[cur]
        assert hop is not None
        fwd_path.append(hop)
        cur = parent  # type: ignore[assignment]
    fwd_path.reverse()

    bwd_path: list[HopRecord] = []
    cur = meeting
    while bwd[cur][0] is not None:
        parent, hop = bwd[cur]
        assert hop is not None
        bwd_path.append(hop)
        cur = parent  # type: ignore[assignment]

    return fwd_path + bwd_path
