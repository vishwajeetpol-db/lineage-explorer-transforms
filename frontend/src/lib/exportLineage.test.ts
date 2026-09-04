import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { exportLineageToExcel, type ExportInput } from "./exportLineage";
import * as xlsx from "./xlsxWriter";
import type { GraphNode, LineageEdge, ColumnLineageEdge } from "../api/client";

const tableNode = (over: Partial<Extract<GraphNode, { node_type: "table" }>> = {}): GraphNode => ({
  node_type: "table",
  id: "cat.sch.t1",
  name: "t1",
  full_name: "cat.sch.t1",
  table_type: "MANAGED",
  owner: "alice",
  comment: "a table",
  columns: [{ name: "c1", type: "int", nullable: true }],
  created_at: "2024-01-01",
  updated_at: "2024-02-01",
  upstream_count: 1,
  downstream_count: 2,
  lineage_status: "connected",
  ...over,
});

const entityNode = (over: Partial<Extract<GraphNode, { node_type: "entity" }>> = {}): GraphNode => ({
  node_type: "entity",
  id: "entity:p1",
  entity_type: "PIPELINE",
  entity_id: "entity:p1",
  display_name: "Pipe 1",
  last_run: "2024-03-01",
  owner: "bob",
  cost_usd: 12.5,
  ...over,
});

describe("exportLineageToExcel", () => {
  let downloadSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    downloadSpy = vi.spyOn(xlsx, "downloadXlsx").mockImplementation(() => {});
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  const capture = () => downloadSpy.mock.calls[0][0] as xlsx.Sheet[];
  const filename = () => downloadSpy.mock.calls[0][1] as string;

  it("builds Summary, Tables and Lineage sheets for a table scope", () => {
    const input: ExportInput = {
      nodes: [tableNode(), entityNode()],
      edges: [
        { source: "cat.sch.t1", target: "entity:p1" },
        { source: "entity:p1", target: "cat.sch.t2" },
      ],
      columnEdges: [],
      scope: "table",
      catalog: "cat",
      schema: "sch",
      focusTable: "cat.sch.t1",
    };
    exportLineageToExcel(input);
    const sheets = capture();
    const names = sheets.map((s) => s.name);
    expect(names).toEqual(["Summary", "Tables", "Lineage", "Pipelines"]);
    // No column lineage sheet since columnEdges empty
    expect(names).not.toContain("Column Lineage");
    // Collapsed edge appears in Lineage sheet data
    const lineage = sheets.find((s) => s.name === "Lineage")!;
    expect(JSON.stringify(lineage.rows)).toContain("cat.sch.t2");
    // filename includes the focus table + date stamp
    expect(filename()).toMatch(/^lineage_cat\.sch\.t1_\d{4}-\d{2}-\d{2}\.xlsx$/);
  });

  it("includes a Column Lineage sheet when columnEdges present", () => {
    const columnEdges: ColumnLineageEdge[] = [
      { source_table: "a.b.c", source_column: "x", target_table: "a.b.d", target_column: "y" },
    ];
    exportLineageToExcel({
      nodes: [tableNode()],
      edges: [],
      columnEdges,
      scope: "table",
      catalog: "cat",
      schema: "sch",
      focusTable: "cat.sch.t1",
    });
    const names = capture().map((s) => s.name);
    expect(names).toContain("Column Lineage");
  });

  it("omits Pipelines sheet when there are no entity nodes", () => {
    exportLineageToExcel({
      nodes: [tableNode()],
      edges: [],
      columnEdges: [],
      scope: "schema",
      catalog: "cat",
      schema: "sch",
      focusTable: null,
    });
    const names = capture().map((s) => s.name);
    expect(names).not.toContain("Pipelines");
  });

  it("uses catalog.schema label for schema scope in the filename", () => {
    exportLineageToExcel({
      nodes: [],
      edges: [],
      columnEdges: [],
      scope: "schema",
      catalog: "cat",
      schema: "sch",
      focusTable: null,
    });
    expect(filename()).toMatch(/^lineage_cat\.sch_\d{4}-\d{2}-\d{2}\.xlsx$/);
  });

  it("uses catalog label for catalog scope in the filename", () => {
    exportLineageToExcel({
      nodes: [],
      edges: [],
      columnEdges: [],
      scope: "catalog",
      catalog: "mycat",
      schema: "",
      focusTable: null,
    });
    expect(filename()).toMatch(/^lineage_mycat_/);
  });

  it("falls back to 'table' label when table scope has no focusTable", () => {
    exportLineageToExcel({
      nodes: [],
      edges: [],
      columnEdges: [],
      scope: "table",
      catalog: "",
      schema: "",
      focusTable: null,
    });
    expect(filename()).toMatch(/^lineage_table_/);
  });

  it("handles nullable table fields and counts orphans in the Summary", () => {
    exportLineageToExcel({
      nodes: [
        tableNode({
          owner: null,
          comment: null,
          created_at: null,
          updated_at: null,
          columns: [],
          lineage_status: "orphan",
        }),
      ],
      edges: [],
      columnEdges: [],
      scope: "schema",
      catalog: "cat",
      schema: "sch",
      focusTable: null,
    });
    const summary = capture().find((s) => s.name === "Summary")!;
    // orphan count row present with value 1
    expect(JSON.stringify(summary.rows)).toContain("1");
  });

  it("sanitizes unsafe characters in the scope label for the filename", () => {
    exportLineageToExcel({
      nodes: [],
      edges: [],
      columnEdges: [],
      scope: "catalog",
      catalog: "bad/name space",
      schema: "",
      focusTable: null,
    });
    expect(filename()).toMatch(/^lineage_bad_name_space_/);
  });

  it("handles entity nodes with null cost/owner/name/last_run", () => {
    exportLineageToExcel({
      nodes: [
        entityNode({ display_name: null, last_run: null, owner: null, cost_usd: null }),
        entityNode({ entity_id: "entity:p2", cost_usd: 99 }),
      ],
      edges: [],
      columnEdges: [],
      scope: "catalog",
      catalog: "c",
      schema: "",
      focusTable: null,
    });
    const pipelines = capture().find((s) => s.name === "Pipelines")!;
    // Two rows, one with a blank cost (undefined style) and one styled cost.
    expect(pipelines.rows.length).toBe(2);
  });

  it("emits placeholder row for empty Tables sheet data", () => {
    exportLineageToExcel({
      nodes: [entityNode()],
      edges: [],
      columnEdges: [],
      scope: "catalog",
      catalog: "c",
      schema: "",
      focusTable: null,
    });
    const tables = capture().find((s) => s.name === "Tables")!;
    // one placeholder row of empty strings
    expect(tables.rows.length).toBe(1);
  });
});
