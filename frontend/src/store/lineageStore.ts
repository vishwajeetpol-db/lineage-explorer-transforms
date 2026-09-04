import { create } from "zustand";
import type { GraphNode, LineageEdge, ColumnLineageEdge, TableSearchItem, SharingOverlay, SharingAudience } from "../api/client";

export type LineageScope = "table" | "schema" | "catalog";

const BUSINESS_VIEW_KEY = "bricktrace-business-view";
const BUSINESS_DETAIL_KEY = "bricktrace-business-detail";

// Read the persisted business-view preference (guarded for jsdom / no-storage).
function readBusinessView(): boolean {
  try {
    return localStorage.getItem(BUSINESS_VIEW_KEY) === "1";
  } catch {
    return false;
  }
}

function persistBusinessView(enabled: boolean): void {
  try {
    localStorage.setItem(BUSINESS_VIEW_KEY, enabled ? "1" : "0");
  } catch {
    /* storage unavailable — in-memory only */
  }
}

function readBusinessDetail(): "data" | "data_and_processing" {
  try {
    return localStorage.getItem(BUSINESS_DETAIL_KEY) === "data" ? "data" : "data_and_processing";
  } catch {
    return "data_and_processing";
  }
}

function persistBusinessDetail(detail: "data" | "data_and_processing"): void {
  try {
    localStorage.setItem(BUSINESS_DETAIL_KEY, detail);
  } catch {
    /* storage unavailable — in-memory only */
  }
}

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
  // Business view: a plain-language lens over the technical graph (relabel +
  // hide detail + simplify) for non-engineers. Off by default; persisted.
  businessView: boolean;
  // Within business view: show only datasets, or datasets + the processing steps
  // (jobs/pipelines/code) that move data between them.
  businessDetail: "data" | "data_and_processing";
  liveMode: boolean;
  isAdmin: boolean;
  discountPercent: number;

  // Delta Sharing overlay — a lens layered on the current graph (off by default).
  sharingEnabled: boolean;
  sharingAudience: SharingAudience;
  sharingOverlay: SharingOverlay | null;

  // Data
  catalogs: string[];
  schemas: string[];
  nodes: GraphNode[];
  edges: LineageEdge[];
  // Precise table→table pairs from the backend (see LineageResponse.table_edges).
  // Used by the "datasets only" business view instead of collapsing entities.
  tableEdges: LineageEdge[];
  columnEdges: ColumnLineageEdge[];

  // Cache metadata
  cached: boolean;
  cachedAt: string | null;
  cacheExpiresAt: string | null;
  fetchDurationMs: number | null;
  lineageWindowDays: number;
  truncated: boolean;
  graphWarnings: Record<string, unknown> | null; // C2–C5 diagnostics from backend
  healthWarning: string | null; // C1: system-table / SP-grant failures

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
  previewOpen: boolean;

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
  setBusinessView: (enabled: boolean) => void;
  toggleBusinessView: () => void;
  setBusinessDetail: (detail: "data" | "data_and_processing") => void;
  setLiveMode: (live: boolean) => void;
  setIsAdmin: (isAdmin: boolean) => void;
  setDiscountPercent: (percent: number) => void;
  setSharingEnabled: (enabled: boolean) => void;
  setSharingAudience: (audience: SharingAudience) => void;
  setSharingOverlay: (overlay: SharingOverlay | null) => void;
  setCatalogs: (catalogs: string[]) => void;
  setSchemas: (schemas: string[]) => void;
  setGraphWarnings: (warnings: Record<string, unknown> | null) => void;
  setHealthWarning: (warning: string | null) => void;
  setLineageData: (data: {
    nodes: GraphNode[];
    edges: LineageEdge[];
    tableEdges?: LineageEdge[];
    cached?: boolean;
    cachedAt?: string | null;
    cacheExpiresAt?: string | null;
    fetchDurationMs?: number | null;
    lineageWindowDays?: number | null;
    truncated?: boolean;
    graphWarnings?: Record<string, unknown> | null;
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
  setPreviewOpen: (open: boolean) => void;
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
  businessView: readBusinessView(),
  businessDetail: readBusinessDetail(),
  liveMode: false,
  isAdmin: false,
  discountPercent: 0,
  // Delta Sharing is always part of the lineage picture — no toggle. The overlay
  // (shared-in/out badges + provider/share/recipient boundary nodes) is fetched
  // and applied automatically whenever a graph loads.
  sharingEnabled: true,
  sharingAudience: "both",
  sharingOverlay: null,
  catalogs: [],
  schemas: [],
  nodes: [],
  edges: [],
  tableEdges: [],
  columnEdges: [],
  cached: false,
  cachedAt: null,
  cacheExpiresAt: null,
  fetchDurationMs: null,
  lineageWindowDays: 90,
  truncated: false,
  graphWarnings: null,
  healthWarning: null,
  loading: false,
  error: null,
  expandedNodes: new Set(),
  selectedNode: null,
  selectedColumn: null,
  hoveredNode: null,
  searchQuery: "",
  searchOpen: false,
  globalSearchOpen: false,
  previewOpen: false,

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
  // Business view hides column-level detail, so entering it also collapses any
  // expanded columns and clears column selection (technical-only state).
  setBusinessView: (enabled) => {
    persistBusinessView(enabled);
    set(enabled
      ? { businessView: true, columnEdges: [], selectedColumn: null, expandedNodes: new Set() }
      : { businessView: false });
  },
  toggleBusinessView: () =>
    set((state) => {
      const next = !state.businessView;
      persistBusinessView(next);
      return next
        ? { businessView: true, columnEdges: [], selectedColumn: null, expandedNodes: new Set() }
        : { businessView: false };
    }),
  setBusinessDetail: (detail) => {
    persistBusinessDetail(detail);
    set({ businessDetail: detail });
  },
  setLiveMode: (live) => set({ liveMode: live }),
  setIsAdmin: (isAdmin) => set({ isAdmin }),
  setDiscountPercent: (percent) => set({ discountPercent: Math.max(0, Math.min(99, percent)) }),
  // Disabling the overlay also drops its data so the graph reflows back immediately.
  setSharingEnabled: (sharingEnabled) => set(sharingEnabled ? { sharingEnabled } : { sharingEnabled, sharingOverlay: null }),
  setSharingAudience: (sharingAudience) => set({ sharingAudience, sharingOverlay: null }),
  setSharingOverlay: (sharingOverlay) => set({ sharingOverlay }),
  setCatalogs: (catalogs) => set({ catalogs }),
  setSchemas: (schemas) => set({ schemas }),
  setGraphWarnings: (graphWarnings) => set({ graphWarnings }),
  setHealthWarning: (healthWarning) => set({ healthWarning }),
  setLineageData: ({ nodes, edges, tableEdges, cached, cachedAt, cacheExpiresAt, fetchDurationMs, lineageWindowDays, truncated, graphWarnings }) =>
    set({
      nodes,
      edges,
      tableEdges: tableEdges ?? [],
      loading: false,
      error: null,
      cached: cached ?? false,
      cachedAt: cachedAt ?? null,
      cacheExpiresAt: cacheExpiresAt ?? null,
      fetchDurationMs: fetchDurationMs ?? null,
      lineageWindowDays: lineageWindowDays ?? 90,
      truncated: truncated ?? false,
      graphWarnings: graphWarnings ?? null,
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
  setPreviewOpen: (previewOpen) => set({ previewOpen }),
  reset: () =>
    set({
      nodes: [],
      edges: [],
      tableEdges: [],
      columnEdges: [],
      expandedNodes: new Set(),
      selectedNode: null,
      selectedColumn: null,
      hoveredNode: null,
      loading: false,
      error: null,
    }),
}));
