import { describe, it, expect, beforeEach } from "vitest";
import { useLineageStore } from "./lineageStore";
import type { GraphNode, LineageEdge, ColumnLineageEdge, TableSearchItem } from "../api/client";

const get = () => useLineageStore.getState();

const tableNode: GraphNode = {
  node_type: "table",
  id: "cat.sch.t1",
  name: "t1",
  full_name: "cat.sch.t1",
  table_type: "MANAGED",
  owner: null,
  comment: null,
  columns: [],
  created_at: null,
  updated_at: null,
  upstream_count: 0,
  downstream_count: 0,
  lineage_status: "connected",
};

const edge: LineageEdge = { source: "a", target: "b" };
const colEdge: ColumnLineageEdge = {
  source_table: "a",
  source_column: "c1",
  target_table: "b",
  target_column: "c2",
};

describe("lineageStore", () => {
  beforeEach(() => {
    // Reset every field to a known initial-ish state.
    get().setFocusTable(null);
    get().reset();
    useLineageStore.setState({
      scope: "table",
      lineageView: "full",
      lineageDepth: 0,
      columnLineageEnabled: false,
      liveMode: false,
      isAdmin: false,
      discountPercent: 0,
      sharingEnabled: true,
      sharingAudience: "both",
      sharingOverlay: null,
      catalogs: [],
      schemas: [],
      allTables: [],
      allTablesLoading: false,
      hoveredNode: null,
      searchQuery: "",
      searchOpen: false,
      globalSearchOpen: false,
      previewOpen: false,
      graphWarnings: null,
      healthWarning: null,
    });
  });

  it("has expected defaults", () => {
    expect(get().focusTable).toBeNull();
    expect(get().scope).toBe("table");
    expect(get().lineageView).toBe("full");
    expect(get().sharingEnabled).toBe(true);
    expect(get().expandedNodes).toBeInstanceOf(Set);
  });

  it("setFocusTable(fqdn) parses catalog/schema and sets table scope", () => {
    get().setFocusTable("cat.sch.tbl");
    expect(get().focusTable).toBe("cat.sch.tbl");
    expect(get().scope).toBe("table");
    expect(get().catalog).toBe("cat");
    expect(get().schema).toBe("sch");
  });

  it("setFocusTable(null) clears graph + selection state", () => {
    get().setFocusTable("cat.sch.tbl");
    get().setLineageData({ nodes: [tableNode], edges: [edge] });
    get().setSelectedNode("x");
    get().setFocusTable(null);
    expect(get().focusTable).toBeNull();
    expect(get().catalog).toBe("");
    expect(get().schema).toBe("");
    expect(get().nodes).toEqual([]);
    expect(get().edges).toEqual([]);
    expect(get().selectedNode).toBeNull();
    expect(get().cached).toBe(false);
  });

  it("enterScopeLineage sets scope + clears focusTable and graph", () => {
    get().setFocusTable("cat.sch.tbl");
    get().enterScopeLineage("schema", "mycat", "mysch");
    expect(get().scope).toBe("schema");
    expect(get().focusTable).toBeNull();
    expect(get().catalog).toBe("mycat");
    expect(get().schema).toBe("mysch");
    expect(get().nodes).toEqual([]);
    expect(get().fetchDurationMs).toBeNull();
  });

  it("setAllTables sets tables and clears loading", () => {
    const tables = [{ full_name: "a.b.c" }] as unknown as TableSearchItem[];
    get().setAllTablesLoading(true);
    get().setAllTables(tables);
    expect(get().allTables).toBe(tables);
    expect(get().allTablesLoading).toBe(false);
  });

  it("setAllTablesLoading toggles loading", () => {
    get().setAllTablesLoading(true);
    expect(get().allTablesLoading).toBe(true);
  });

  it("setCatalog resets schema + graph", () => {
    get().setSchema("old");
    get().setLineageData({ nodes: [tableNode], edges: [] });
    get().setCatalog("newcat");
    expect(get().catalog).toBe("newcat");
    expect(get().schema).toBe("");
    expect(get().schemas).toEqual([]);
    expect(get().nodes).toEqual([]);
  });

  it("setSchema resets graph but keeps catalog", () => {
    get().setCatalog("c");
    get().setLineageData({ nodes: [tableNode], edges: [] });
    get().setSchema("s");
    expect(get().schema).toBe("s");
    expect(get().nodes).toEqual([]);
  });

  it("setLineageView clears column edges + selection", () => {
    get().setColumnEdges([colEdge]);
    get().setSelectedColumn({ table: "a", column: "c" });
    get().setLineageView("table");
    expect(get().lineageView).toBe("table");
    expect(get().columnEdges).toEqual([]);
    expect(get().selectedColumn).toBeNull();
  });

  it("setLineageDepth sets depth", () => {
    get().setLineageDepth(3);
    expect(get().lineageDepth).toBe(3);
  });

  it("setColumnLineageEnabled resets column state", () => {
    get().setColumnEdges([colEdge]);
    get().setColumnLineageEnabled(true);
    expect(get().columnLineageEnabled).toBe(true);
    expect(get().columnEdges).toEqual([]);
  });

  it("setLiveMode / setIsAdmin toggle flags", () => {
    get().setLiveMode(true);
    get().setIsAdmin(true);
    expect(get().liveMode).toBe(true);
    expect(get().isAdmin).toBe(true);
  });

  it("setDiscountPercent clamps to 0..99", () => {
    get().setDiscountPercent(50);
    expect(get().discountPercent).toBe(50);
    get().setDiscountPercent(-5);
    expect(get().discountPercent).toBe(0);
    get().setDiscountPercent(150);
    expect(get().discountPercent).toBe(99);
  });

  it("setSharingEnabled(true) keeps overlay, (false) drops it", () => {
    get().setSharingOverlay({ foo: "bar" } as any);
    get().setSharingEnabled(true);
    expect(get().sharingEnabled).toBe(true);
    expect(get().sharingOverlay).not.toBeNull();
    get().setSharingEnabled(false);
    expect(get().sharingEnabled).toBe(false);
    expect(get().sharingOverlay).toBeNull();
  });

  it("setSharingAudience clears overlay", () => {
    get().setSharingOverlay({ foo: "bar" } as any);
    get().setSharingAudience("in");
    expect(get().sharingAudience).toBe("in");
    expect(get().sharingOverlay).toBeNull();
  });

  it("setSharingOverlay sets overlay", () => {
    get().setSharingOverlay({ foo: 1 } as any);
    expect(get().sharingOverlay).toEqual({ foo: 1 });
  });

  it("setCatalogs / setSchemas set arrays", () => {
    get().setCatalogs(["c1", "c2"]);
    get().setSchemas(["s1"]);
    expect(get().catalogs).toEqual(["c1", "c2"]);
    expect(get().schemas).toEqual(["s1"]);
  });

  it("setGraphWarnings / setHealthWarning set values", () => {
    get().setGraphWarnings({ c2: true });
    get().setHealthWarning("down");
    expect(get().graphWarnings).toEqual({ c2: true });
    expect(get().healthWarning).toBe("down");
  });

  it("setLineageData applies data + defaults", () => {
    get().setLoading(true);
    get().setLineageData({ nodes: [tableNode], edges: [edge] });
    expect(get().nodes).toEqual([tableNode]);
    expect(get().edges).toEqual([edge]);
    expect(get().loading).toBe(false);
    expect(get().error).toBeNull();
    expect(get().cached).toBe(false);
    expect(get().lineageWindowDays).toBe(90);
    expect(get().truncated).toBe(false);
  });

  it("setLineageData honors provided optional fields", () => {
    get().setLineageData({
      nodes: [],
      edges: [],
      cached: true,
      cachedAt: "t0",
      cacheExpiresAt: "t1",
      fetchDurationMs: 42,
      lineageWindowDays: 30,
      truncated: true,
      graphWarnings: { w: 1 },
    });
    expect(get().cached).toBe(true);
    expect(get().cachedAt).toBe("t0");
    expect(get().cacheExpiresAt).toBe("t1");
    expect(get().fetchDurationMs).toBe(42);
    expect(get().lineageWindowDays).toBe(30);
    expect(get().truncated).toBe(true);
    expect(get().graphWarnings).toEqual({ w: 1 });
  });

  it("setColumnEdges sets edges", () => {
    get().setColumnEdges([colEdge]);
    expect(get().columnEdges).toEqual([colEdge]);
  });

  it("setLoading sets loading", () => {
    get().setLoading(true);
    expect(get().loading).toBe(true);
  });

  it("setError sets error + clears loading", () => {
    get().setLoading(true);
    get().setError("boom");
    expect(get().error).toBe("boom");
    expect(get().loading).toBe(false);
  });

  it("toggleNodeExpanded adds then removes a node", () => {
    get().toggleNodeExpanded("n1");
    expect(get().expandedNodes.has("n1")).toBe(true);
    get().toggleNodeExpanded("n1");
    expect(get().expandedNodes.has("n1")).toBe(false);
  });

  it("collapsing a node clears its column selection + column edges", () => {
    get().toggleNodeExpanded("n1");
    get().setSelectedColumn({ table: "n1", column: "c" });
    get().setColumnEdges([colEdge]);
    get().toggleNodeExpanded("n1"); // collapse
    expect(get().expandedNodes.has("n1")).toBe(false);
    expect(get().selectedColumn).toBeNull();
    expect(get().columnEdges).toEqual([]);
  });

  it("collapsing a node keeps unrelated column selection + edges", () => {
    get().toggleNodeExpanded("n1");
    get().setSelectedColumn({ table: "other", column: "c" });
    get().setColumnEdges([colEdge]);
    get().toggleNodeExpanded("n1"); // collapse
    expect(get().selectedColumn).toEqual({ table: "other", column: "c" });
    expect(get().columnEdges).toEqual([colEdge]);
  });

  it("setSelectedNode / setSelectedColumn / setHoveredNode", () => {
    get().setSelectedNode("n");
    get().setSelectedColumn({ table: "t", column: "c" });
    get().setHoveredNode("h");
    expect(get().selectedNode).toBe("n");
    expect(get().selectedColumn).toEqual({ table: "t", column: "c" });
    expect(get().hoveredNode).toBe("h");
  });

  it("search + preview UI setters", () => {
    get().setSearchQuery("q");
    get().setSearchOpen(true);
    get().setGlobalSearchOpen(true);
    get().setPreviewOpen(true);
    expect(get().searchQuery).toBe("q");
    expect(get().searchOpen).toBe(true);
    expect(get().globalSearchOpen).toBe(true);
    expect(get().previewOpen).toBe(true);
  });

  it("reset clears graph + selection but keeps selectors", () => {
    get().setFocusTable("cat.sch.tbl");
    get().setLineageData({ nodes: [tableNode], edges: [edge] });
    get().toggleNodeExpanded("n1");
    get().setSelectedNode("n");
    get().setError("e");
    get().reset();
    expect(get().nodes).toEqual([]);
    expect(get().edges).toEqual([]);
    expect(get().columnEdges).toEqual([]);
    expect(get().expandedNodes.size).toBe(0);
    expect(get().selectedNode).toBeNull();
    expect(get().error).toBeNull();
    expect(get().loading).toBe(false);
    // Selectors retained
    expect(get().catalog).toBe("cat");
  });
});
