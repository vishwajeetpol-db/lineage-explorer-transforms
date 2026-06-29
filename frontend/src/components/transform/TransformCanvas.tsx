/**
 * TransformCanvas — React Flow sub-graph for transformation lineage.
 *
 * Renders a vertical DAG (top-to-bottom) where:
 * - Level 0 (top) = target column (the one the user clicked)
 * - Level N (below) = Nth upstream source columns, cascading downward
 * - Edges show the transformation category + expression persistently,
 *   with the full expression + source file on hover
 *
 * Supports pruning:
 * - Category filtering (hiddenCategories from store → edges hidden)
 * - Path isolation (isolatedNodeId from store → only path to target shown)
 * - Node click → triggers path isolation
 *
 * Uses a simple layered layout (ELK.js integration available via Web Worker).
 */
import { useCallback, useEffect, useMemo } from 'react';
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  Node,
  Edge,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
} from 'reactflow';
import 'reactflow/dist/style.css';
import { TransformResponse, TransformLevel } from '../../api/transform';
import { useTransformStore } from '../../store/transformStore';
import TransformNodeComponent from './TransformNode';
import TransformEdgeComponent from './TransformEdge';

// Layout cell footprint — kept close to the real rendered node box (CSS
// minWidth 140 / maxWidth 200, auto height ~56) so fitView's bounding box
// matches what's actually drawn. H_GAP/V_GAP are the clear channels between
// siblings (horizontal) and levels (vertical).
const NODE_WIDTH = 180;
const NODE_HEIGHT = 56;
const H_GAP = 80;
const V_GAP = 64;

const nodeTypes = {
  transformNode: TransformNodeComponent,
};

const edgeTypes = {
  transformEdge: TransformEdgeComponent,
};

interface TransformCanvasProps {
  data: TransformResponse;
  height?: number;
}

/**
 * BFS to find all node IDs on any path between `startId` and the target
 * (depth 0 node). Works backwards through edges (target ← source).
 */
function findPathNodes(
  targetNodeId: string,
  isolatedId: string,
  edges: Edge[],
): Set<string> {
  // Build adjacency: for each node, which nodes are its upstream sources?
  // Edge direction: source → target (source feeds into target)
  const upstreamOf = new Map<string, Set<string>>(); // target → set of sources
  for (const e of edges) {
    if (!upstreamOf.has(e.target)) upstreamOf.set(e.target, new Set());
    upstreamOf.get(e.target)!.add(e.source);
  }

  // BFS from target upward to find isolatedId
  // We need all nodes on ANY path from target to isolatedId
  // Strategy: find all ancestors of target that are also descendants of isolatedId
  // Simpler: BFS from target tracking paths, mark nodes on paths reaching isolatedId

  // Step 1: Find all ancestors of target (BFS up)
  const ancestorsOfTarget = new Set<string>();
  const queue: string[] = [targetNodeId];
  ancestorsOfTarget.add(targetNodeId);
  while (queue.length > 0) {
    const curr = queue.shift()!;
    for (const src of upstreamOf.get(curr) || []) {
      if (!ancestorsOfTarget.has(src)) {
        ancestorsOfTarget.add(src);
        queue.push(src);
      }
    }
  }

  // Step 2: Find all descendants of isolatedId (BFS down)
  const downstreamOf = new Map<string, Set<string>>(); // source → set of targets
  for (const e of edges) {
    if (!downstreamOf.has(e.source)) downstreamOf.set(e.source, new Set());
    downstreamOf.get(e.source)!.add(e.target);
  }

  const descendantsOfIsolated = new Set<string>();
  const queue2: string[] = [isolatedId];
  descendantsOfIsolated.add(isolatedId);
  while (queue2.length > 0) {
    const curr = queue2.shift()!;
    for (const tgt of downstreamOf.get(curr) || []) {
      if (!descendantsOfIsolated.has(tgt)) {
        descendantsOfIsolated.add(tgt);
        queue2.push(tgt);
      }
    }
  }

  // Intersection = nodes on paths between isolatedId and target
  const pathNodes = new Set<string>();
  for (const n of ancestorsOfTarget) {
    if (descendantsOfIsolated.has(n)) {
      pathNodes.add(n);
    }
  }
  // Also include target itself
  pathNodes.add(targetNodeId);

  return pathNodes;
}

/**
 * Convert TransformResponse levels into React Flow nodes + edges.
 */
function buildFlowGraph(data: TransformResponse) {
  const nodes: Node[] = [];
  const edges: Edge[] = [];
  const nodeSet = new Set<string>();

  for (const level of data.levels) {
    for (const node of level.nodes) {
      if (nodeSet.has(node.node_id)) continue;
      nodeSet.add(node.node_id);

      const tableParts = node.table_fqn.split('.');
      const shortTable = tableParts.length === 3 ? tableParts[2] : node.table_fqn;

      nodes.push({
        id: node.node_id,
        type: 'transformNode',
        position: { x: 0, y: 0 },
        data: {
          column: node.column,
          tableFqn: node.table_fqn,
          shortTable,
          depth: level.depth,
          color: level.color,
          label: level.label,
          isTarget: level.depth === 0,
        },
        sourcePosition: Position.Top,
        targetPosition: Position.Bottom,
      });
    }

    for (const transform of level.transforms) {
      const edgeId = `${transform.source_node_id}->${transform.target_node_id}`;
      edges.push({
        id: edgeId,
        source: transform.source_node_id,
        target: transform.target_node_id,
        type: 'transformEdge',
        animated: true,
        data: {
          expression: transform.expression,
          category: transform.category,
          categoryColor: transform.category_color,
          sourceFile: transform.source_file,
        },
        style: {
          stroke: transform.category_color,
          strokeWidth: 2,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: transform.category_color,
          width: 16,
          height: 16,
        },
      });
    }
  }

  return { nodes, edges };
}

/**
 * Simple layered layout: places nodes in rows by depth, centered horizontally.
 */
function applySimpleLayout(nodes: Node[], levels: TransformLevel[]): Node[] {
  const depthGroups = new Map<number, Node[]>();

  for (const node of nodes) {
    const depth = node.data.depth as number;
    if (!depthGroups.has(depth)) depthGroups.set(depth, []);
    depthGroups.get(depth)!.push(node);
  }

  const layoutNodes: Node[] = [];

  for (const [depth, group] of depthGroups) {
    const rowWidth = group.length * (NODE_WIDTH + H_GAP);
    const startX = -rowWidth / 2;
    // Target column (depth 0) sits at the TOP; upstream sources cascade
    // downward as depth increases. This matches the node handles
    // (source emits from Top, target receives at Bottom) so edges run as
    // clean vertical connectors instead of curving back on themselves.
    const y = depth * (NODE_HEIGHT + V_GAP);

    group.forEach((node, i) => {
      layoutNodes.push({
        ...node,
        position: {
          x: startX + i * (NODE_WIDTH + H_GAP),
          y,
        },
      });
    });
  }

  return layoutNodes;
}

export default function TransformCanvas({ data, height = 500 }: TransformCanvasProps) {
  const hiddenCategories = useTransformStore((s) => s.hiddenCategories);
  const isolatedNodeId = useTransformStore((s) => s.isolatedNodeId);
  const isolateNode = useTransformStore((s) => s.isolateNode);

  // Build raw graph
  const { nodes: rawNodes, edges: rawEdges } = useMemo(() => buildFlowGraph(data), [data]);

  // Compute path isolation set
  const pathNodeIds = useMemo(() => {
    if (!isolatedNodeId) return null;
    // Find the target node (depth 0)
    const targetNode = rawNodes.find((n) => n.data.isTarget);
    if (!targetNode) return null;
    return findPathNodes(targetNode.id, isolatedNodeId, rawEdges);
  }, [isolatedNodeId, rawNodes, rawEdges]);

  // Apply category filtering and path isolation
  const filteredEdges = useMemo(() => {
    return rawEdges.map((edge) => {
      const category = edge.data?.category as string;
      const isCategoryHidden = hiddenCategories.has(category);

      const isPathHidden = pathNodeIds
        ? !pathNodeIds.has(edge.source) || !pathNodeIds.has(edge.target)
        : false;

      const isHidden = isCategoryHidden || isPathHidden;

      return {
        ...edge,
        hidden: isHidden,
        style: {
          ...edge.style,
          opacity: isHidden ? 0.08 : 1,
        },
        animated: !isHidden,
      };
    });
  }, [rawEdges, hiddenCategories, pathNodeIds]);

  const filteredNodes = useMemo(() => {
    return rawNodes.map((node) => {
      const isPathDimmed = pathNodeIds ? !pathNodeIds.has(node.id) : false;
      return {
        ...node,
        data: {
          ...node.data,
          isDimmed: isPathDimmed,
        },
        style: {
          ...node.style,
          opacity: isPathDimmed ? 0.15 : 1,
        },
      };
    });
  }, [rawNodes, pathNodeIds]);

  // Apply layout
  const layoutNodes = useMemo(
    () => applySimpleLayout(filteredNodes, data.levels),
    [filteredNodes, data.levels],
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layoutNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(filteredEdges);

  // Update when data or filters change
  useEffect(() => {
    setNodes(layoutNodes);
    setEdges(filteredEdges);
  }, [layoutNodes, filteredEdges, setNodes, setEdges]);

  // Fit view on initial load. Cap the zoom-IN ceiling (maxZoom) so a small
  // graph (2-5 nodes) can't be magnified to the giant 3x default — while still
  // allowing zoom-OUT (minZoom 0.2) to fit a deep graph. This re-fit is also
  // what re-frames the graph when the user switches columns.
  const onInit = useCallback((instance: any) => {
    setTimeout(() => instance.fitView({ padding: 0.2, maxZoom: 1.2, minZoom: 0.2 }), 100);
  }, []);

  // Node click → path isolation (click target node to clear)
  const onNodeClick = useCallback(
    (_: any, node: Node) => {
      if (node.data.isTarget) {
        // Clicking target clears isolation
        isolateNode(null);
      } else {
        // Clicking any other node isolates its path to target
        isolateNode(node.id);
      }
    },
    [isolateNode],
  );

  return (
    <div style={{ height: `${height}px`, width: '100%' }} className="rounded-lg overflow-hidden border border-slate-700">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onInit={onInit}
        fitView
        fitViewOptions={{ padding: 0.2, minZoom: 0.2, maxZoom: 1.2 }}
        minZoom={0.2}
        maxZoom={1.5}
        defaultEdgeOptions={{ animated: true }}
        proOptions={{ hideAttribution: true }}
      >
        <Background color="#1e293b" gap={20} size={1} />
        <Controls className="!bg-slate-800 !border-slate-700" />
        <MiniMap
          nodeColor={(n) => n.data?.color || '#6366f1'}
          className="!bg-slate-900 !border-slate-700"
          maskColor="rgba(0, 0, 0, 0.7)"
        />
      </ReactFlow>
    </div>
  );
}
