import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { TableNode } from "../../api/client";
import ReactFlow, {
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  useReactFlow,
  useUpdateNodeInternals,
  applyNodeChanges,
  type Node,
  type Edge,
  type NodeTypes,
  type EdgeTypes,
  type NodeChange,
} from "reactflow";
import "reactflow/dist/style.css";
import { AnimatePresence, motion } from "framer-motion";
import { RotateCcw, Code2, Briefcase, Lightbulb } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { isHiddenInBusinessView, businessNodeLabel, businessNodeType } from "../../lib/businessView";
import LineageExplainModal from "./LineageExplainModal";
import { api } from "../../api/client";
import { layoutGraph } from "../../lib/elkLayout";

import TableNodeComponent from "./TableNode";
import type { SharingBadge } from "./TableNode";
import EntityNodeComponent from "./EntityNode";
import SharingNodeComponent from "./SharingNode";
import AnimatedEdge from "./AnimatedEdge";
import TableTooltip from "../ui/TableTooltip";
import SearchDialog from "../ui/SearchDialog";
import Skeleton from "../ui/Skeleton";

const nodeTypes: NodeTypes = {
  tableNode: TableNodeComponent,
  entityNode: EntityNodeComponent,
  sharingNode: SharingNodeComponent,
};

const edgeTypes: EdgeTypes = {
  animated: AnimatedEdge,
};

function LineageCanvas() {
  const {
    nodes: allNodes,
    edges: allEdges,
    tableEdges,
    focusTable,
    lineageView,
    lineageDepth,
    columnEdges,
    expandedNodes,
    selectedNode,
    selectedColumn,
    hoveredNode,
    loading,
    error,
    columnLineageEnabled,
    businessView,
    businessDetail,
    setBusinessView,
    setBusinessDetail,
    setSelectedNode,
    setSelectedColumn,
    setColumnEdges,
  } = useLineageStore();

  // AI "explain this lineage" modal (Business-view lightbulb).
  const [explainOpen, setExplainOpen] = useState(false);

  // Subgraph extraction: when focusTable is set, show only its lineage path.
  // lineageDepth controls how many table-to-table hops to show (0 = full).
  // Entity nodes are transparent — they don't count as a hop.
  const { rawNodes, rawEdges } = useMemo(() => {
    if (!focusTable || allNodes.length === 0) return { rawNodes: allNodes, rawEdges: allEdges };

    const isEntity = (id: string) => id.startsWith("entity:");
    const maxDepth = lineageDepth > 0 ? lineageDepth : Infinity;

    // Build adjacency from full schema edges
    const upstream = new Map<string, string[]>();
    const downstream = new Map<string, string[]>();
    for (const e of allEdges) {
      if (!upstream.has(e.target)) upstream.set(e.target, []);
      upstream.get(e.target)!.push(e.source);
      if (!downstream.has(e.source)) downstream.set(e.source, []);
      downstream.get(e.source)!.push(e.target);
    }

    // Depth-limited BFS — entity nodes are transparent (depth only increments on table hops)
    const connected = new Set<string>();
    connected.add(focusTable);

    // Trace upstream — stop exploring beyond a table at maxDepth
    const visitedUp = new Set<string>();
    const qUp: [string, number][] = [[focusTable, 0]];
    while (qUp.length > 0) {
      const [node, depth] = qUp.shift()!;
      if (visitedUp.has(node)) continue;
      visitedUp.add(node);
      connected.add(node);
      // If this is a table at max depth, don't explore further
      if (!isEntity(node) && depth >= maxDepth) continue;
      for (const src of upstream.get(node) || []) {
        if (!visitedUp.has(src)) {
          const nextDepth = isEntity(src) ? depth : depth + 1;
          if (nextDepth <= maxDepth) qUp.push([src, nextDepth]);
        }
      }
    }

    // Trace downstream — stop exploring beyond a table at maxDepth
    const visitedDown = new Set<string>();
    const qDown: [string, number][] = [[focusTable, 0]];
    while (qDown.length > 0) {
      const [node, depth] = qDown.shift()!;
      if (visitedDown.has(node)) continue;
      visitedDown.add(node);
      connected.add(node);
      // If this is a table at max depth, don't explore further
      if (!isEntity(node) && depth >= maxDepth) continue;
      for (const tgt of downstream.get(node) || []) {
        if (!visitedDown.has(tgt)) {
          const nextDepth = isEntity(tgt) ? depth : depth + 1;
          if (nextDepth <= maxDepth) qDown.push([tgt, nextDepth]);
        }
      }
    }

    // Expand entity neighborhoods: when an entity is discovered (e.g. a pipeline
    // found via upstream BFS), include ALL its direct neighbors (sources + targets)
    // so the entity shows full context — not just the path to the focused table.
    for (const nodeId of [...connected]) {
      if (!isEntity(nodeId)) continue;
      for (const src of upstream.get(nodeId) || []) connected.add(src);
      for (const tgt of downstream.get(nodeId) || []) connected.add(tgt);
    }

    const filteredNodes = allNodes.filter((n) => connected.has(n.id));
    const filteredEdges = allEdges.filter((e) => connected.has(e.source) && connected.has(e.target));
    return { rawNodes: filteredNodes, rawEdges: filteredEdges };
  }, [allNodes, allEdges, focusTable, lineageDepth]);

  // Apply view mode filter on top of subgraph extraction
  const { viewNodes: baseNodes, viewEdges: baseEdges } = useMemo(() => {
    const isEntity = (id: string) => id.startsWith("entity:");

    if (lineageView === "table") {
      // Table-only: remove entity nodes, use directTableEdges
      const tableNodes = rawNodes.filter((n) => n.node_type !== "entity");
      // Collapse entity edges into direct table→table
      const entitySources = new Map<string, Set<string>>();
      const entityTargets = new Map<string, Set<string>>();
      const directEdges: { source: string; target: string }[] = [];
      const edgeSet = new Set<string>();

      for (const e of rawEdges) {
        if (!isEntity(e.source) && !isEntity(e.target)) {
          const k = `${e.source}|${e.target}`;
          if (!edgeSet.has(k)) { edgeSet.add(k); directEdges.push(e); }
        } else if (!isEntity(e.source) && isEntity(e.target)) {
          if (!entitySources.has(e.target)) entitySources.set(e.target, new Set());
          entitySources.get(e.target)!.add(e.source);
        } else if (isEntity(e.source) && !isEntity(e.target)) {
          if (!entityTargets.has(e.source)) entityTargets.set(e.source, new Set());
          entityTargets.get(e.source)!.add(e.target);
        }
      }
      for (const [eid, sources] of entitySources) {
        const targets = entityTargets.get(eid) || new Set();
        for (const s of sources) {
          for (const t of targets) {
            if (s === t) continue;
            const k = `${s}|${t}`;
            if (!edgeSet.has(k)) { edgeSet.add(k); directEdges.push({ source: s, target: t }); }
          }
        }
      }
      return { viewNodes: tableNodes, viewEdges: directEdges };
    }

    if (lineageView === "pipeline") {
      // Pipeline-only: show entity nodes with dependencies derived from shared tables
      const entityNodes = rawNodes.filter((n) => n.node_type === "entity");
      // Build: entity → writes (target tables), entity → reads (source tables)
      const entityWrites = new Map<string, Set<string>>(); // entity → tables it writes
      const entityReads = new Map<string, Set<string>>();  // entity → tables it reads
      for (const e of rawEdges) {
        if (!isEntity(e.source) && isEntity(e.target)) {
          // table → entity: entity READS this table
          if (!entityReads.has(e.target)) entityReads.set(e.target, new Set());
          entityReads.get(e.target)!.add(e.source);
        } else if (isEntity(e.source) && !isEntity(e.target)) {
          // entity → table: entity WRITES this table
          if (!entityWrites.has(e.source)) entityWrites.set(e.source, new Set());
          entityWrites.get(e.source)!.add(e.target);
        }
      }
      // If entity A writes table T and entity B reads table T, then A → B
      const pipelineEdges: { source: string; target: string }[] = [];
      const peSet = new Set<string>();
      for (const [writerEntity, writtenTables] of entityWrites) {
        for (const table of writtenTables) {
          for (const [readerEntity, readTables] of entityReads) {
            if (readerEntity !== writerEntity && readTables.has(table)) {
              const k = `${writerEntity}|${readerEntity}`;
              if (!peSet.has(k)) { peSet.add(k); pipelineEdges.push({ source: writerEntity, target: readerEntity }); }
            }
          }
        }
      }
      return { viewNodes: entityNodes, viewEdges: pipelineEdges };
    }

    // "full" mode — both tables and entities as-is
    return { viewNodes: rawNodes, viewEdges: rawEdges };
  }, [rawNodes, rawEdges, lineageView]);

  // Business view simplification: drop the noisiest technical nodes (e.g. ad-hoc
  // QUERY entities) and bridge the flow through them so the picture stays
  // connected — source → target — without the low-level clutter. Column-level
  // detail is already suppressed via the store when business view is enabled.
  const { viewNodes, viewEdges } = useMemo(() => {
    if (!businessView) return { viewNodes: baseNodes, viewEdges: baseEdges };
    // "data" = hide every processing step (entity) and connect datasets directly.
    // "data_and_processing" = keep jobs/pipelines, drop only the noisiest (queries).
    const dataOnly = businessDetail === "data";
    const hidden = new Set(
      baseNodes
        .filter((n) => (dataOnly ? n.node_type === "entity" : isHiddenInBusinessView(n)))
        .map((n) => n.id)
    );
    if (hidden.size === 0) return { viewNodes: baseNodes, viewEdges: baseEdges };

    const kept = baseNodes.filter((n) => !hidden.has(n.id));

    // Data-only: use the backend's PRECISE table→table pairs (filtered to the
    // datasets on screen) instead of cross-producting each entity's inputs ×
    // outputs — the cross-product fabricates edges and turns a hub table into a
    // dense mesh. Fall back to bridging only if the backend didn't supply pairs
    // (e.g. an older cached trace).
    if (dataOnly && tableEdges.length > 0) {
      const keptIds = new Set(kept.map((n) => n.id));
      const seenT = new Set<string>();
      const edges: { source: string; target: string }[] = [];
      for (const e of tableEdges) {
        if (e.source === e.target) continue;
        if (!keptIds.has(e.source) || !keptIds.has(e.target)) continue;
        const k = `${e.source}|${e.target}`;
        if (!seenT.has(k)) { seenT.add(k); edges.push({ source: e.source, target: e.target }); }
      }
      return { viewNodes: kept, viewEdges: edges };
    }
    const incoming = new Map<string, Set<string>>(); // hidden id → its sources
    const outgoing = new Map<string, Set<string>>(); // hidden id → its targets
    const passthrough: { source: string; target: string }[] = [];
    const seen = new Set<string>();
    const add = (source: string, target: string) => {
      const k = `${source}|${target}`;
      if (source !== target && !seen.has(k)) { seen.add(k); passthrough.push({ source, target }); }
    };

    for (const e of baseEdges) {
      const sh = hidden.has(e.source);
      const th = hidden.has(e.target);
      if (!sh && !th) { add(e.source, e.target); continue; }
      if (th) { if (!incoming.has(e.target)) incoming.set(e.target, new Set()); incoming.get(e.target)!.add(e.source); }
      if (sh) { if (!outgoing.has(e.source)) outgoing.set(e.source, new Set()); outgoing.get(e.source)!.add(e.target); }
    }
    // Bridge s → [hidden] → t into s → t (only across still-visible endpoints).
    for (const h of hidden) {
      for (const s of incoming.get(h) || []) {
        if (hidden.has(s)) continue;
        for (const t of outgoing.get(h) || []) {
          if (hidden.has(t)) continue;
          add(s, t);
        }
      }
    }
    return { viewNodes: kept, viewEdges: passthrough };
  }, [baseNodes, baseEdges, tableEdges, businessView, businessDetail]);

  // Plain-language projection of the on-screen graph for the AI explanation.
  // Memoized so the modal's fetch effect doesn't re-fire on every parent render.
  const explainNodes = useMemo(
    () => viewNodes.map((n) => ({ id: n.id, label: businessNodeLabel(n), type: businessNodeType(n) })),
    [viewNodes]
  );
  const explainEdges = useMemo(
    () => viewEdges.map((e) => ({ source: e.source, target: e.target })),
    [viewEdges]
  );

  // =========================================================================
  // DELTA SHARING OVERLAY — augment the view graph with sharing boundary nodes.
  // Outbound: table → [share] → recipient (teal, dashed). Inbound: tag tables
  // whose catalog is a Delta Share foreign catalog and add a [provider] node.
  // This is a lens, not lineage — augmentation happens AFTER the view-mode memo
  // so it flows uniformly through layout/reveal/highlight, but never touches the
  // canonical lineage data or the focus-table BFS above.
  // =========================================================================
  const sharingEnabled = useLineageStore((s) => s.sharingEnabled);
  const sharingAudience = useLineageStore((s) => s.sharingAudience);
  const sharingOverlay = useLineageStore((s) => s.sharingOverlay);

  const { augNodes, augEdges, badgeByTable } = useMemo(() => {
    const badgeByTable = new Map<string, SharingBadge>();
    if (!sharingEnabled || !sharingOverlay) {
      return { augNodes: viewNodes as Node["data"][], augEdges: viewEdges, badgeByTable };
    }

    const tableIds = new Set(viewNodes.filter((n) => n.node_type === "table").map((n) => n.id));
    const extraNodes: any[] = [];
    const extraEdges: { source: string; target: string; sharing: true }[] = [];
    const seen = new Set<string>();
    const addNode = (id: string, kind: "share" | "recipient" | "provider", label: string, sub?: string | null) => {
      if (seen.has(id)) return;
      seen.add(id);
      extraNodes.push({ id, node_type: "sharing", sharingKind: kind, label, sub: sub ?? null });
    };

    // Outbound — my tables published into shares
    for (const so of sharingOverlay.shared_out) {
      if (!tableIds.has(so.full_name)) continue;
      const b = badgeByTable.get(so.full_name) ?? {};
      b.out = b.out ?? { shares: [], recipients: [] };
      if (!b.out.shares.includes(so.share_name)) b.out.shares.push(so.share_name);
      for (const r of so.recipients) if (!b.out.recipients.includes(r)) b.out.recipients.push(r);
      badgeByTable.set(so.full_name, b);

      const shareId = `sharing:share:${so.share_name}`;
      addNode(shareId, "share", so.share_name, so.shared_as ? `as ${so.shared_as}` : null);
      extraEdges.push({ source: so.full_name, target: shareId, sharing: true });
      for (const r of so.recipients) {
        const recId = `sharing:recipient:${r}`;
        addNode(recId, "recipient", r);
        extraEdges.push({ source: shareId, target: recId, sharing: true });
      }
    }

    // Inbound — tables whose catalog is a Delta Share foreign catalog
    const foreignByCat = new Map(sharingOverlay.foreign_catalogs.map((f) => [f.catalog_name, f]));
    for (const n of viewNodes) {
      if (n.node_type !== "table") continue;
      const cat = n.id.split(".")[0];
      const f = foreignByCat.get(cat);
      if (!f) continue;
      const b = badgeByTable.get(n.id) ?? {};
      b.in = { provider: f.provider_name, shares: f.share_names };
      badgeByTable.set(n.id, b);
      const provId = `sharing:provider:${f.provider_name}`;
      addNode(provId, "provider", f.provider_name, f.region ? `${f.cloud ?? ""} ${f.region}`.trim() : null);
      extraEdges.push({ source: provId, target: n.id, sharing: true });
    }

    return {
      augNodes: [...viewNodes, ...extraNodes] as any[],
      augEdges: [...viewEdges, ...extraEdges],
      badgeByTable,
    };
  }, [viewNodes, viewEdges, sharingEnabled, sharingOverlay]);

  const [flowNodes, setFlowNodes] = useState<Node[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [tooltipData, setTooltipData] = useState<{
    node: TableNode;
    position: { x: number; y: number };
  } | null>(null);
  const [revealCounter, setRevealCounter] = useState(-1);
  const [layoutKey, setLayoutKey] = useState(0);
  const reactFlowInstance = useReactFlow();
  const updateNodeInternals = useUpdateNodeInternals();
  const tooltipTimer = useRef<ReturnType<typeof setTimeout>>();
  const tooltipHideTimer = useRef<ReturnType<typeof setTimeout>>();
  const [showLargeHint, setShowLargeHint] = useState(false);
  const flowNodesRef = useRef<Node[]>(flowNodes);
  flowNodesRef.current = flowNodes;

  // Allow dragging nodes by applying position changes
  const onNodesChange = useCallback(
    (changes: NodeChange[]) => {
      setFlowNodes((nds) => applyNodeChanges(changes, nds));
    },
    [setFlowNodes]
  );

  // Pre-compute adjacency maps for O(1) lookups (avoids O(n²) on every hover/select)
  const adjacency = useMemo(() => {
    const upstream = new Map<string, string[]>();  // target -> sources
    const downstream = new Map<string, string[]>(); // source -> targets
    for (const e of augEdges) {
      if (!upstream.has(e.target)) upstream.set(e.target, []);
      upstream.get(e.target)!.push(e.source);
      if (!downstream.has(e.source)) downstream.set(e.source, []);
      downstream.get(e.source)!.push(e.target);
    }
    return { upstream, downstream };
  }, [augEdges]);

  // Compute connected nodes for highlighting using adjacency maps
  // Entity nodes: one-hop only (source tables + target tables)
  // Table nodes: full transitive traversal
  const isEntityScope = (selectedNode || hoveredNode || "").startsWith("entity:");

  const connectedNodes = useMemo(() => {
    if (!selectedNode && !hoveredNode) return new Set<string>();
    const target = selectedNode || hoveredNode;
    const connected = new Set<string>();
    if (target) {
      connected.add(target);

      if (target.startsWith("entity:")) {
        // Entity node: one hop only — direct source and target tables
        for (const src of adjacency.upstream.get(target) || []) {
          connected.add(src);
        }
        for (const tgt of adjacency.downstream.get(target) || []) {
          connected.add(tgt);
        }
      } else {
        // Table node: full transitive traversal
        const findUpstream = (nodeId: string) => {
          for (const src of adjacency.upstream.get(nodeId) || []) {
            if (!connected.has(src)) {
              connected.add(src);
              findUpstream(src);
            }
          }
        };
        const findDownstream = (nodeId: string) => {
          for (const tgt of adjacency.downstream.get(nodeId) || []) {
            if (!connected.has(tgt)) {
              connected.add(tgt);
              findDownstream(tgt);
            }
          }
        };
        findUpstream(target);
        findDownstream(target);
      }
    }
    return connected;
  }, [selectedNode, hoveredNode, adjacency]);

  const connectedEdges = useMemo(() => {
    if (!selectedNode && !hoveredNode) return new Set<string>();
    const edgeSet = new Set<string>();
    augEdges.forEach((e) => {
      if (connectedNodes.has(e.source) && connectedNodes.has(e.target)) {
        edgeSet.add(`${e.source}->${e.target}`);
      }
    });
    return edgeSet;
  }, [connectedNodes, augEdges, selectedNode, hoveredNode]);

  // Schema-level column lineage from system.access.column_lineage.
  // Fetched once per schema (cached server-side), then transitive traversal
  // runs client-side on real UC column edges — not name-matching heuristics.
  const [schemaColEdges, setSchemaColEdges] = useState<{ source_table: string; source_column: string; target_table: string; target_column: string }[]>([]);

  // Fetch column edges when column mode is on. A trace spans MANY schemas across
  // catalogs, so we fetch column lineage for EVERY distinct catalog.schema present
  // in the graph and merge — otherwise column tracing would stop at the focused
  // table's own schema. Catalog-wide scope is skipped (Columns is disabled there).
  const storeCatalog = useLineageStore((s) => s.catalog);
  const storeSchema = useLineageStore((s) => s.schema);
  const scope = useLineageStore((s) => s.scope);
  useEffect(() => {
    if (!columnLineageEnabled || scope === "catalog") {
      setSchemaColEdges([]);
      return;
    }
    // Distinct catalog.schema pairs from the table nodes currently in the graph.
    const pairs = new Set<string>();
    for (const n of allNodes) {
      if (n.node_type === "table") {
        const p = n.id.split(".");
        if (p.length === 3) pairs.add(`${p[0]}.${p[1]}`);
      }
    }
    if (pairs.size === 0) { setSchemaColEdges([]); return; }

    let cancelled = false;
    Promise.all(
      [...pairs].slice(0, 16).map((cs) => {
        const dot = cs.indexOf(".");
        const c = cs.slice(0, dot), s = cs.slice(dot + 1);
        return api.getSchemaColumnLineage(c, s).then((r) => r.edges).catch(() => []);
      })
    ).then((lists) => { if (!cancelled) setSchemaColEdges(lists.flat()); });
    return () => { cancelled = true; };
  }, [columnLineageEnabled, allNodes, scope]);

  // Fetch the Delta Sharing overlay when the toggle is on. Catalog scope has no
  // single schema, so the overlay is queried catalog-wide (schema undefined).
  const setSharingOverlay = useLineageStore((s) => s.setSharingOverlay);
  useEffect(() => {
    if (!sharingEnabled) { setSharingOverlay(null); return; }
    const cat = focusTable ? focusTable.split(".")[0] : storeCatalog;
    const sch = scope === "catalog" ? undefined : (focusTable ? focusTable.split(".")[1] : storeSchema);
    if (!cat) { setSharingOverlay(null); return; }

    let cancelled = false;
    api.getSharingOverlay(cat, sch, sharingAudience)
      .then((o) => { if (!cancelled) setSharingOverlay(o); })
      .catch(() => { if (!cancelled) setSharingOverlay(null); });
    return () => { cancelled = true; };
  }, [sharingEnabled, sharingAudience, focusTable, storeCatalog, storeSchema, scope, setSharingOverlay]);

  // Build adjacency maps for O(1) column lineage traversal (computed once when edges change)
  const colAdjacency = useMemo(() => {
    // target "table.col" → list of edges feeding into it
    const upstream = new Map<string, typeof schemaColEdges>();
    // source "table.col" → list of edges flowing out of it
    const downstream = new Map<string, typeof schemaColEdges>();
    for (const e of schemaColEdges) {
      const tgtKey = `${e.target_table}.${e.target_column.toLowerCase()}`;
      const srcKey = `${e.source_table}.${e.source_column.toLowerCase()}`;
      if (!upstream.has(tgtKey)) upstream.set(tgtKey, []);
      upstream.get(tgtKey)!.push(e);
      if (!downstream.has(srcKey)) downstream.set(srcKey, []);
      downstream.get(srcKey)!.push(e);
    }
    return { upstream, downstream };
  }, [schemaColEdges]);

  // Transitive column trace on real UC edges via adjacency maps (O(1) per hop)
  useEffect(() => {
    if (!selectedColumn || schemaColEdges.length === 0) {
      setColumnEdges([]);
      return;
    }
    const { table: selTable, column: selCol } = selectedColumn;
    const result: typeof schemaColEdges = [];
    const seen = new Set<string>();

    const traceUpstream = (tbl: string, col: string) => {
      const key = `${tbl}.${col.toLowerCase()}`;
      for (const e of colAdjacency.upstream.get(key) || []) {
        const edgeKey = `${e.source_table}.${e.source_column}|${e.target_table}.${e.target_column}`;
        if (!seen.has(edgeKey)) {
          seen.add(edgeKey);
          result.push(e);
          traceUpstream(e.source_table, e.source_column);
        }
      }
    };

    const traceDownstream = (tbl: string, col: string) => {
      const key = `${tbl}.${col.toLowerCase()}`;
      for (const e of colAdjacency.downstream.get(key) || []) {
        const edgeKey = `${e.source_table}.${e.source_column}|${e.target_table}.${e.target_column}`;
        if (!seen.has(edgeKey)) {
          seen.add(edgeKey);
          result.push(e);
          traceDownstream(e.target_table, e.target_column);
        }
      }
    };

    traceUpstream(selTable, selCol);
    traceDownstream(selTable, selCol);
    setColumnEdges(result);
  }, [selectedColumn, schemaColEdges, colAdjacency, setColumnEdges]);

  // =========================================================================
  // LAYOUT EFFECT — runs ONLY when raw data changes or reset is pressed.
  // NEVER runs on expand/collapse (expandedNodes is NOT a dependency).
  //
  // Cancellation: AbortController aborts the previous ELK layout when the user
  // switches schemas before it finishes. Without this, an older layout's
  // Promise can resolve AFTER a newer one and overwrite the graph with stale
  // data. fitView retry timers are also tracked so they don't fire on a graph
  // that's already been replaced.
  // =========================================================================
  useEffect(() => {
    if (augNodes.length === 0) {
      setFlowNodes([]);
      setFlowEdges([]);
      return;
    }

    const controller = new AbortController();
    const fitViewTimers: ReturnType<typeof setTimeout>[] = [];

    const rfNodes: Node[] = augNodes.map((n: any) => ({
      id: n.id,
      type: n.node_type === "entity" ? "entityNode" : n.node_type === "sharing" ? "sharingNode" : "tableNode",
      position: { x: 0, y: 0 },
      data: {
        ...n,
        isExpanded: false,
        isSelected: false,
        isHighlighted: true,
        isDimmed: false,
        ...(n.node_type === "table" ? { sharingBadge: badgeByTable.get(n.id) } : {}),
      },
    }));

    // Entity and sharing nodes use default (unkeyed) handles; only table nodes
    // expose the explicit table/column handle ids.
    const isSpecial = (id: string) => id.startsWith("entity:") || id.startsWith("sharing:");
    const rfEdges: Edge[] = augEdges.map((e: any) => ({
      id: e.sharing ? `sharing-e-${e.source}-${e.target}` : `e-${e.source}-${e.target}`,
      source: e.source,
      target: e.target,
      // Explicit handle IDs prevent React Flow from routing table edges through column handles
      sourceHandle: isSpecial(e.source) ? undefined : `${e.source}__table__source`,
      targetHandle: isSpecial(e.target) ? undefined : `${e.target}__table__target`,
      type: "animated",
      data: { isHighlighted: false, isDimmed: false, isColumnEdge: false, isVisible: true, isSharing: !!e.sharing },
    }));

    // ELK layout — always uses collapsed dimensions for stable positioning.
    // Runs in a Web Worker so large graphs don't block the main thread.
    layoutGraph(rfNodes, rfEdges, new Set(), controller.signal)
      .then(({ nodes, edges }) => {
        if (controller.signal.aborted) return;
        const isLargeGraph = nodes.length > 50;

        if (isLargeGraph) {
          // Large graphs: reveal all at once to avoid 275+ re-render cycles
          const readyNodes = nodes.map((n) => ({
            ...n,
            data: { ...n.data, revealOrder: 0, isRevealed: true },
          }));
          const readyEdges = edges.map((e) => ({
            ...e,
            data: { ...e.data, isVisible: true },
          }));
          setFlowNodes(readyNodes);
          setFlowEdges(readyEdges);
          setRevealCounter(Infinity); // skip staggered reveal
          // React Flow needs time to measure all node dimensions before fitView works.
          // Retry fitView at increasing intervals to handle slow renders on large graphs.
          const fitViewRetries = [100, 500, 1000, 2000];
          fitViewRetries.forEach((delay) => {
            const t = setTimeout(() => {
              if (!controller.signal.aborted) {
                reactFlowInstance.fitView({ padding: 0.15, duration: 300 });
              }
            }, delay);
            fitViewTimers.push(t);
          });
        } else {
          // Small graphs: staggered reveal left-to-right
          const sorted = [...nodes].sort((a, b) => a.position.x - b.position.x);
          const orderMap = new Map<string, number>();
          sorted.forEach((n, i) => orderMap.set(n.id, i));

          const revealNodes = nodes.map((n) => ({
            ...n,
            data: {
              ...n.data,
              revealOrder: orderMap.get(n.id) ?? 0,
              isRevealed: false,
            },
          }));

          const revealEdges = edges.map((e) => ({
            ...e,
            data: { ...e.data, isVisible: false },
          }));

          setFlowNodes(revealNodes);
          setFlowEdges(revealEdges);
          setRevealCounter(-1);
        }
      })
      .catch((err) => {
        if (err?.name !== "AbortError") {
          console.error("Layout failed:", err);
        }
      });

    return () => {
      controller.abort();
      fitViewTimers.forEach(clearTimeout);
    };
    // expandedNodes is intentionally NOT in the dependency array.
    // Expand/collapse is handled by a separate effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [augNodes, augEdges, badgeByTable, reactFlowInstance, layoutKey]);

  // =========================================================================
  // EXPAND/COLLAPSE EFFECT — updates node data in place without re-running ELK.
  // After framer-motion animation completes (~350ms), force React Flow to
  // recalculate handle positions so edges route correctly.
  // =========================================================================
  useEffect(() => {
    setFlowNodes((prev) => {
      if (prev.length === 0) return prev;
      return prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isExpanded: expandedNodes.has(n.id),
        },
      }));
    });

    // Wait for framer-motion AnimatePresence height animation to finish,
    // then tell React Flow to re-measure all handle positions.
    const timer = setTimeout(() => {
      flowNodesRef.current.forEach((n) => updateNodeInternals(n.id));
    }, 350);
    return () => clearTimeout(timer);
  }, [expandedNodes, updateNodeInternals]);

  // Staggered reveal: increment counter every 50ms to reveal nodes left-to-right
  useEffect(() => {
    if (revealCounter < 0 && flowNodes.length > 0 && flowNodes.some((n) => !n.data.isRevealed)) {
      setRevealCounter(0);
      return;
    }
    if (revealCounter < 0) return;

    const maxOrder = Math.max(...flowNodes.map((n) => n.data.revealOrder ?? 0), 0);
    if (revealCounter > maxOrder) {
      // All nodes revealed — fit viewport after DOM has painted node dimensions.
      // Double requestAnimationFrame ensures the browser has completed at least
      // one paint cycle with the revealed nodes before we calculate bounds.
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          reactFlowInstance.fitView({ padding: 0.15, duration: 400 });
        });
      });
      // Safety net: if the first fitView fired before React Flow measured all
      // node dimensions (slow device, large graph), retry after a generous delay.
      // If the first one succeeded, this is a no-op (viewport already correct).
      const safetyTimer = setTimeout(() => {
        reactFlowInstance.fitView({ padding: 0.15, duration: 300 });
      }, 600);
      return () => clearTimeout(safetyTimer);
    }

    const timer = setInterval(() => {
      setRevealCounter((c) => {
        if (c > maxOrder) {
          clearInterval(timer);
          return c;
        }
        return c + 1;
      });
    }, 50);
    return () => clearInterval(timer);
  }, [revealCounter, flowNodes.length, reactFlowInstance]);

  // Update revealed state on nodes and edge visibility based on revealCounter
  useEffect(() => {
    if (revealCounter < 0) return;

    setFlowNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isRevealed: (n.data.revealOrder ?? 0) <= revealCounter,
        },
      }))
    );

    setFlowEdges((prev) => {
      // Build node lookup map once (O(n)) instead of .find() per edge (O(n*m))
      const nodeOrderMap = new Map<string, number>();
      for (const n of flowNodesRef.current) {
        nodeOrderMap.set(n.id, n.data.revealOrder ?? 0);
      }
      return prev.map((e) => {
        const sourceRevealed = (nodeOrderMap.get(e.source) ?? 0) <= revealCounter;
        const targetRevealed = (nodeOrderMap.get(e.target) ?? 0) <= revealCounter;
        return {
          ...e,
          data: {
            ...e.data,
            isVisible: sourceRevealed && targetRevealed,
          },
        };
      });
    });
  }, [revealCounter]);

  // Update node/edge styling on select/hover — preserves isVisible and isRevealed
  useEffect(() => {
    if (flowNodes.length === 0) return;

    const hasHighlight = selectedNode || hoveredNode;

    setFlowNodes((prev) =>
      prev.map((n) => ({
        ...n,
        data: {
          ...n.data,
          isSelected: n.id === selectedNode,
          isHighlighted: !hasHighlight || connectedNodes.has(n.id),
          isDimmed: !!hasHighlight && !connectedNodes.has(n.id),
        },
      }))
    );

    setFlowEdges((prev) => {
      const tableEdges = prev
        .filter((e) => !e.id.startsWith("col-e-"))
        .map((e) => {
          const edgeKey = `${e.source}->${e.target}`;
          const isHl = !hasHighlight || connectedEdges.has(edgeKey);
          const isPipelineEdge = isEntityScope && isHl && !!hasHighlight;
          return {
            ...e,
            data: {
              ...e.data, // preserves isVisible
              isHighlighted: !!hasHighlight && isHl,
              isDimmed: !!hasHighlight && !isHl,
              isColumnEdge: false,
              isPipelineEdge,
            },
          };
        });

      if (selectedColumn && columnEdges.length > 0) {
        columnEdges.forEach((ce, i) => {
          tableEdges.push({
            id: `col-e-${i}`,
            source: ce.source_table,
            sourceHandle: `${ce.source_table}__col__${ce.source_column}__source`,
            target: ce.target_table,
            targetHandle: `${ce.target_table}__col__${ce.target_column}__target`,
            type: "animated",
            data: { isHighlighted: false, isDimmed: false, isColumnEdge: true, isVisible: true },
          });
        });
      }

      return tableEdges;
    });
  }, [selectedNode, hoveredNode, connectedNodes, connectedEdges, columnEdges, selectedColumn]);

  // Tooltip on hover (table nodes only — entity nodes show inline info).
  // When the cursor leaves the node we DELAY hiding (tooltipHideTimer) so the
  // user can move onto the tooltip to select/copy text; hovering the tooltip
  // cancels that timer (see onMouseEnter on TableTooltip below).
  useEffect(() => {
    if (hoveredNode && !selectedNode && !hoveredNode.startsWith("entity:")) {
      clearTimeout(tooltipHideTimer.current);
      tooltipTimer.current = setTimeout(() => {
        const node = viewNodes.find((n) => n.id === hoveredNode);
        const rfNode = flowNodes.find((n) => n.id === hoveredNode);
        if (node && rfNode) {
          const viewportPos = reactFlowInstance.flowToScreenPosition({
            x: rfNode.position.x + 220,
            y: rfNode.position.y,
          });
          setTooltipData({ node: node as TableNode, position: viewportPos });
        }
      }, 300);
      return () => clearTimeout(tooltipTimer.current);
    }
    clearTimeout(tooltipTimer.current);
    tooltipHideTimer.current = setTimeout(() => setTooltipData(null), 260);
    return () => clearTimeout(tooltipHideTimer.current);
  }, [hoveredNode, selectedNode, viewNodes, flowNodes, reactFlowInstance]);

  // Large-graph hint: show when the graph is big, auto-hide after 6s.
  useEffect(() => {
    if (viewNodes.length > 300) {
      setShowLargeHint(true);
      const t = setTimeout(() => setShowLargeHint(false), 6000);
      return () => clearTimeout(t);
    }
    setShowLargeHint(false);
  }, [viewNodes.length]);

  const handlePaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedColumn(null);
  }, [setSelectedNode, setSelectedColumn]);

  const handleNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      // Entity nodes are always selectable (pipeline scope highlighting)
      // Table nodes are selectable only when column lineage is off
      if (node.id.startsWith("entity:") || !columnLineageEnabled) {
        setSelectedNode(selectedNode === node.id ? null : node.id);
      }
    },
    [columnLineageEnabled, selectedNode, setSelectedNode]
  );

  // Double-click a TABLE node to re-focus on it (works even in column mode).
  // The Table Lineage workspace watches `selectedNode` to refocus its panels;
  // in the main app this just selects the node (harmless).
  const handleNodeDoubleClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (!node.id.startsWith("entity:") && !node.id.startsWith("path:")) {
        setSelectedNode(node.id);
      }
    },
    [setSelectedNode]
  );

  const handleResetLayout = useCallback(() => {
    setSelectedNode(null);
    setSelectedColumn(null);
    setLayoutKey((k) => k + 1);
  }, [setSelectedNode, setSelectedColumn]);

  const handleSearchSelect = useCallback(
    (nodeId: string) => {
      const rfNode = flowNodes.find((n) => n.id === nodeId);
      if (rfNode) {
        reactFlowInstance.setCenter(
          rfNode.position.x + 110,
          rfNode.position.y + 24,
          { zoom: 1.5, duration: 600 }
        );
        setSelectedNode(nodeId);
      }
    },
    [flowNodes, reactFlowInstance, setSelectedNode]
  );

  if (loading) return <Skeleton />;

  if (error) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="text-center">
          <div className="text-red-400 text-[14px] font-medium mb-2">Error loading lineage</div>
          <div className="text-slate-500 text-[13px] max-w-[400px]">{error}</div>
        </div>
      </div>
    );
  }

  if (viewNodes.length === 0 && !loading) {
    return (
      <div className="absolute inset-0 flex items-center justify-center">
        <div className="text-center">
          <motion.div
            animate={{ opacity: [0.15, 0.25, 0.15] }}
            transition={{ duration: 4, repeat: Infinity, ease: "easeInOut" }}
            className="mb-6"
          >
            <div className="w-16 h-16 mx-auto rounded-2xl bg-gradient-to-br from-accent/20 to-purple-500/20 border border-white/[0.04] flex items-center justify-center">
              <GitBranchPlaceholder />
            </div>
          </motion.div>
          <div className="text-slate-400 text-[15px] font-medium tracking-tight">
            Pick a table to explore its lineage
          </div>
          <div className="text-slate-600 text-[12px] mt-2 max-w-[300px] leading-relaxed">
            Search (⌘K) or browse to any table — its full end-to-end lineage loads automatically, across catalogs and schemas.
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onPaneClick={handlePaneClick}
        onNodeClick={handleNodeClick}
        onNodeDoubleClick={handleNodeDoubleClick}
        nodesDraggable
        minZoom={0.1}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
        defaultEdgeOptions={{ animated: false }}
      >
        <Background
          variant={BackgroundVariant.Dots}
          gap={24}
          size={0.8}
          color="rgba(255,255,255,0.03)"
        />
        <Controls showInteractive={false} />
        <MiniMap
          nodeStrokeWidth={3}
          zoomable
          pannable
          style={{ width: 160, height: 100 }}
        />
      </ReactFlow>

      {/* Technical ⇄ Business view controls — flip into a plain-language lens for
          non-engineers, choose how much to show, and explain the graph with AI. */}
      <div className="absolute top-3 left-3 z-20 flex flex-col items-start gap-2">
        <div className="flex items-center gap-2">
          <div
            role="group"
            aria-label="Lineage detail level"
            className="flex items-center gap-0.5 rounded-lg bg-surface-100/90 backdrop-blur-md border border-white/[0.06] p-0.5 shadow-[0_2px_12px_rgba(0,0,0,0.3)]"
          >
            <button
              onClick={() => setBusinessView(false)}
              aria-pressed={!businessView}
              title="Technical view — full engineering detail"
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                !businessView
                  ? "bg-accent/20 text-accent-light"
                  : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.05]"
              }`}
            >
              <Code2 size={13} />
              Technical
            </button>
            <button
              onClick={() => setBusinessView(true)}
              aria-pressed={businessView}
              title="Business view — plain-language, simplified for non-engineers"
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                businessView
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.05]"
              }`}
            >
              <Briefcase size={13} />
              Business
            </button>
          </div>

          {/* AI explain-the-lineage bulb — business view only */}
          {businessView && (
            <button
              onClick={() => setExplainOpen(true)}
              title="Explain this lineage in plain English (AI)"
              aria-label="Explain this lineage with AI"
              className="flex items-center gap-1.5 px-2.5 py-[7px] rounded-lg bg-surface-100/90 backdrop-blur-md border border-amber-500/25 text-amber-300 hover:bg-amber-500/10 hover:border-amber-400/40 transition-colors shadow-[0_2px_12px_rgba(0,0,0,0.3)]"
            >
              <Lightbulb size={14} />
              <span className="text-[11px] font-medium">Explain</span>
            </button>
          )}
        </div>

        {/* Data-only vs Data + processing — business view only */}
        {businessView && (
          <div
            role="group"
            aria-label="Business view content"
            className="flex items-center gap-0.5 rounded-lg bg-surface-100/90 backdrop-blur-md border border-white/[0.06] p-0.5 shadow-[0_2px_12px_rgba(0,0,0,0.3)]"
          >
            <button
              onClick={() => setBusinessDetail("data")}
              aria-pressed={businessDetail === "data"}
              title="Show only the datasets and how they connect"
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                businessDetail === "data"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.05]"
              }`}
            >
              Data only
            </button>
            <button
              onClick={() => setBusinessDetail("data_and_processing")}
              aria-pressed={businessDetail === "data_and_processing"}
              title="Show datasets plus the processing steps that move data between them"
              className={`px-2.5 py-1 rounded-md text-[11px] font-medium transition-colors ${
                businessDetail === "data_and_processing"
                  ? "bg-emerald-500/20 text-emerald-300"
                  : "text-slate-500 hover:text-slate-300 hover:bg-white/[0.05]"
              }`}
            >
              Data + processing
            </button>
          </div>
        )}
      </div>

      <LineageExplainModal
        open={explainOpen}
        onClose={() => setExplainOpen(false)}
        focusTable={focusTable || "the selected datasets"}
        nodes={explainNodes}
        edges={explainEdges}
        detail={businessDetail}
      />

      {/* Large-graph hint — auto-hides after 6s via state (below). Layout of many
          nodes can briefly freeze the UI; this signals that's expected, not a bug. */}
      <AnimatePresence>
        {showLargeHint && (
          <motion.div
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.3 }}
            className="
              absolute top-3 left-1/2 -translate-x-1/2 z-20
              flex items-center gap-2 px-3 py-1.5 rounded-lg
              bg-amber-500/10 border border-amber-500/25 backdrop-blur-md
              text-amber-200 text-[11px] font-medium
              shadow-[0_2px_12px_rgba(0,0,0,0.3)]
            "
          >
            Large graph ({viewNodes.length} nodes) — layout may take a few seconds
          </motion.div>
        )}
      </AnimatePresence>

      {/* Reset Layout button */}
      <button
        onClick={handleResetLayout}
        title="Reset layout"
        className="
          absolute bottom-[140px] left-3 z-10
          flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg
          bg-surface-100/90 backdrop-blur-md border border-white/[0.06]
          hover:border-white/[0.15] hover:bg-surface-200
          text-slate-500 hover:text-slate-300
          transition-all duration-200 group
          shadow-[0_2px_12px_rgba(0,0,0,0.3)]
        "
      >
        <RotateCcw size={13} className="group-hover:rotate-[-180deg] transition-transform duration-500" />
        <span className="text-[10px] font-medium tracking-wide">Reset</span>
      </button>

      <AnimatePresence>
        {tooltipData && (
          <TableTooltip
            node={tooltipData.node}
            position={tooltipData.position}
            onMouseEnter={() => clearTimeout(tooltipHideTimer.current)}
            onMouseLeave={() => setTooltipData(null)}
          />
        )}
      </AnimatePresence>

      <SearchDialog onSelectNode={handleSearchSelect} />
    </>
  );
}

function GitBranchPlaceholder() {
  return (
    <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" className="text-slate-600 mx-auto">
      <line x1="6" y1="3" x2="6" y2="15" />
      <circle cx="18" cy="6" r="3" />
      <circle cx="6" cy="18" r="3" />
      <path d="M18 9a9 9 0 0 1-9 9" />
    </svg>
  );
}

export default memo(LineageCanvas);
