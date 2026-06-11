import { create } from "zustand";
import type { GraphNode, LineageEdge, ColumnLineageEdge, TableSearchItem } from "../api/client";

export type LineageScope = "table" | "schema" | "catalog";

interface LineageState {
  // Table-focused landing
  focusTable: string | null; // FQDN of selected table
  // Scope of the currently rendered graph. "table" = focused on focusTable's
  // lineage path; "schema" = whole schema; "catalog" = whole catalog.
  scope: LineageScope;
  allTables: TableSearchItem[];
  allTablesLoading: boolean;

  // Selectors
  catalog: string;
  schema: string;
  lineageView: "pipeline" | "table" | "full";
  lineageDepth: number; // 0 = full lineage, >0 = N hops upstream + N hops downstream
  columnLineageEnabled: boolean;
  liveMode: boolean;
  isAdmin: boolean;
  discountPercent: number;

  // Data
  catalogs: string[];
  schemas: string[];
  nodes: GraphNode[];
  edges: LineageEdge[];
  columnEdges: ColumnLineageEdge[];

  // Cache metadata
  cached: boolean;
  cachedAt: string | null;
  cacheExpiresAt: string | null;
  fetchDurationMs: number | null;

  // UI state
  loading: boolean;
  error: string | null;
  expandedNodes: Set<string>;
  selectedNode: string | null;
  selectedColumn: { table: string; column: string } | null;
  hoveredNode: string | null;
  searchQuery: string;
  searchOpen: boolean;
  globalSearchOpen: boolean;

  // Actions
  setFocusTable: (fqdn: string | null) => void;
  // Enter a non-table scope (whole schema or whole catalog). Clears focusTable
  // and the current graph so the canvas renders the full unfiltered graph.
  enterScopeLineage: (scope: "schema" | "catalog", catalog: string, schema: string) => void;
  setAllTables: (tables: TableSearchItem[]) => void;
  setAllTablesLoading: (loading: boolean) => void;
  setCatalog: (catalog: string) => void;
  setSchema: (schema: string) => void;
  setLineageView: (view: "pipeline" | "table" | "full") => void;
  setLineageDepth: (depth: number) => void;
  setColumnLineageEnabled: (enabled: boolean) => void;
  setLiveMode: (live: boolean) => void;
  setIsAdmin: (isAdmin: boolean) => void;
  setDiscountPercent: (percent: number) => void;
  setCatalogs: (catalogs: string[]) => void;
  setSchemas: (schemas: string[]) => void;
  setLineageData: (data: {
    nodes: GraphNode[];
    edges: LineageEdge[];
    cached?: boolean;
    cachedAt?: string | null;
    cacheExpiresAt?: string | null;
    fetchDurationMs?: number | null;
  }) => void;
  setColumnEdges: (edges: ColumnLineageEdge[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  toggleNodeExpanded: (nodeId: string) => void;
  setSelectedNode: (nodeId: string | null) => void;
  setSelectedColumn: (col: { table: string; column: string } | null) => void;
  setHoveredNode: (nodeId: string | null) => void;
  setSearchQuery: (query: string) => void;
  setSearchOpen: (open: boolean) => void;
  setGlobalSearchOpen: (open: boolean) => void;
  reset: () => void;
}

export const useLineageStore = create<LineageState>((set) => ({
  focusTable: null,
  scope: "table",
  allTables: [],
  allTablesLoading: false,
  catalog: "",
  schema: "",
  lineageView: "full",
  lineageDepth: 0,
  columnLineageEnabled: false,
  liveMode: false,
  isAdmin: false,
  discountPercent: 0,
  catalogs: [],
  schemas: [],
  nodes: [],
  edges: [],
  columnEdges: [],
  cached: false,
  cachedAt: null,
  cacheExpiresAt: null,
  fetchDurationMs: null,
  loading: false,
  error: null,
  expandedNodes: new Set(),
  selectedNode: null,
  selectedColumn: null,
  hoveredNode: null,
  searchQuery: "",
  searchOpen: false,
  globalSearchOpen: false,

  setFocusTable: (fqdn) => {
    if (!fqdn) {
      set({ focusTable: null, scope: "table", catalog: "", schema: "", nodes: [], edges: [], columnEdges: [], expandedNodes: new Set(), selectedNode: null, selectedColumn: null, cached: false, cachedAt: null, cacheExpiresAt: null, fetchDurationMs: null });
    } else {
      const parts = fqdn.split(".");
      set({ focusTable: fqdn, scope: "table", catalog: parts[0], schema: parts[1] });
    }
  },
  enterScopeLineage: (scope, catalog, schema) =>
    set({
      scope,
      focusTable: null,
      catalog,
      schema,
      nodes: [],
      edges: [],
      columnEdges: [],
      expandedNodes: new Set(),
      selectedNode: null,
      selectedColumn: null,
      cached: false,
      cachedAt: null,
      cacheExpiresAt: null,
      fetchDurationMs: null,
    }),
  setAllTables: (tables) => set({ allTables: tables, allTablesLoading: false }),
  setAllTablesLoading: (loading) => set({ allTablesLoading: loading }),
  setCatalog: (catalog) => set({ catalog, schema: "", schemas: [], nodes: [], edges: [], columnEdges: [], expandedNodes: new Set(), selectedNode: null, selectedColumn: null, cached: false, cachedAt: null, cacheExpiresAt: null, fetchDurationMs: null }),
  setSchema: (schema) => set({ schema, nodes: [], edges: [], columnEdges: [], expandedNodes: new Set(), selectedNode: null, selectedColumn: null, cached: false, cachedAt: null, cacheExpiresAt: null, fetchDurationMs: null }),
  setLineageView: (view) => set({ lineageView: view, columnEdges: [], selectedColumn: null, expandedNodes: new Set() }),
  setLineageDepth: (depth) => set({ lineageDepth: depth }),
  setColumnLineageEnabled: (enabled) => set({ columnLineageEnabled: enabled, columnEdges: [], selectedColumn: null, expandedNodes: new Set() }),
  setLiveMode: (live) => set({ liveMode: live }),
  setIsAdmin: (isAdmin) => set({ isAdmin }),
  setDiscountPercent: (percent) => set({ discountPercent: Math.max(0, Math.min(99, percent)) }),
  setCatalogs: (catalogs) => set({ catalogs }),
  setSchemas: (schemas) => set({ schemas }),
  setLineageData: ({ nodes, edges, cached, cachedAt, cacheExpiresAt, fetchDurationMs }) =>
    set({
      nodes,
      edges,
      loading: false,
      error: null,
      cached: cached ?? false,
      cachedAt: cachedAt ?? null,
      cacheExpiresAt: cacheExpiresAt ?? null,
      fetchDurationMs: fetchDurationMs ?? null,
    }),
  setColumnEdges: (columnEdges) => set({ columnEdges }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  toggleNodeExpanded: (nodeId) =>
    set((state) => {
      const next = new Set(state.expandedNodes);
      if (next.has(nodeId)) {
        next.delete(nodeId);
        // Clear column selection if collapsing the selected table
        const newSelectedColumn =
          state.selectedColumn?.table === nodeId ? null : state.selectedColumn;
        return { expandedNodes: next, selectedColumn: newSelectedColumn, columnEdges: newSelectedColumn ? state.columnEdges : [] };
      } else {
        next.add(nodeId);
        return { expandedNodes: next };
      }
    }),
  setSelectedNode: (nodeId) => set({ selectedNode: nodeId }),
  setSelectedColumn: (col) => set({ selectedColumn: col }),
  setHoveredNode: (nodeId) => set({ hoveredNode: nodeId }),
  setSearchQuery: (searchQuery) => set({ searchQuery }),
  setSearchOpen: (searchOpen) => set({ searchOpen }),
  setGlobalSearchOpen: (globalSearchOpen) => set({ globalSearchOpen }),
  reset: () =>
    set({
      nodes: [],
      edges: [],
      columnEdges: [],
      expandedNodes: new Set(),
      selectedNode: null,
      selectedColumn: null,
      hoveredNode: null,
      loading: false,
      error: null,
    }),
}));
