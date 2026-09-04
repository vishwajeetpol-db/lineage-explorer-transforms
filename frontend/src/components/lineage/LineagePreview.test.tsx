import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LineagePreview from "./LineagePreview";
import { useLineageStore } from "../../store/lineageStore";
import { exportLineageToExcel } from "../../lib/exportLineage";
import type { TableNode, EntityNode, LineageEdge, ColumnLineageEdge } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
vi.mock("../../lib/exportLineage", () => ({ exportLineageToExcel: vi.fn() }));

function tbl(name: string): TableNode {
  return {
    node_type: "table", id: name, name, full_name: `main.sales.${name}`, table_type: "MANAGED",
    owner: "o", comment: null, columns: [{ name: "c", type: "int", nullable: false }],
    created_at: null, updated_at: null, upstream_count: 1, downstream_count: 2, lineage_status: "connected",
  };
}
const entity: EntityNode = { node_type: "entity", id: "e1", entity_type: "JOB", entity_id: "123", display_name: "My Job", last_run: "2024", owner: null, cost_usd: 42 };
const edges: LineageEdge[] = [{ source: "main.sales.a", target: "main.sales.b" }];
const colEdges: ColumnLineageEdge[] = [{ source_table: "main.sales.a", source_column: "x", target_table: "main.sales.b", target_column: "y" }];

function seed(overrides: any = {}) {
  useLineageStore.setState({
    previewOpen: true,
    nodes: [tbl("a"), tbl("b"), entity],
    edges,
    columnEdges: colEdges,
    scope: "schema",
    catalog: "main",
    schema: "sales",
    ...overrides,
  });
}

describe("LineagePreview", () => {
  beforeEach(() => {
    (exportLineageToExcel as any).mockReset();
    seed();
    global.fetch = vi.fn();
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders nothing when closed", () => {
    useLineageStore.setState({ previewOpen: false });
    render(<LineagePreview />);
    expect(screen.queryByText("Export preview")).not.toBeInTheDocument();
  });

  it("renders tabs and the tables grid", () => {
    render(<LineagePreview />);
    expect(screen.getByText("Export preview")).toBeInTheDocument();
    expect(screen.getByText("Tables")).toBeInTheDocument();
    expect(screen.getByText("Lineage")).toBeInTheDocument();
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
    expect(screen.getByText("Columns")).toBeInTheDocument();
    expect(screen.getByText("main.sales.a")).toBeInTheDocument();
  });

  it("switches tabs to pipelines and columns", async () => {
    const user = userEvent.setup();
    render(<LineagePreview />);
    await user.click(screen.getByText("Pipelines"));
    expect(screen.getByText("My Job")).toBeInTheDocument();
    await user.click(screen.getByText("Columns"));
    expect(screen.getByText("Source column")).toBeInTheDocument();
  });

  it("filters and sorts rows", async () => {
    const user = userEvent.setup();
    render(<LineagePreview />);
    const filter = screen.getByPlaceholderText("Filter rows...");
    fireEvent.change(filter, { target: { value: "main.sales.a" } });
    expect(screen.getByText("main.sales.a")).toBeInTheDocument();
    expect(screen.queryByText("main.sales.b")).not.toBeInTheDocument();
    fireEvent.change(filter, { target: { value: "zzz" } });
    expect(screen.getByText("No rows")).toBeInTheDocument();
    fireEvent.change(filter, { target: { value: "" } });
    // sort by clicking a header
    await user.click(screen.getByText("Full name"));
    await user.click(screen.getByText("Full name"));
  });

  it("closes via the close button", async () => {
    const user = userEvent.setup();
    render(<LineagePreview />);
    await user.click(screen.getByLabelText("Close"));
    expect(useLineageStore.getState().previewOpen).toBe(false);
  });

  it("downloads via server export", async () => {
    (global.fetch as any).mockResolvedValue({
      ok: true,
      blob: async () => new Blob(["x"]),
      headers: { get: () => 'attachment; filename="lineage.xlsx"' },
    });
    (URL as any).createObjectURL = vi.fn(() => "blob:x");
    (URL as any).revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    const user = userEvent.setup();
    render(<LineagePreview />);
    await user.click(screen.getByText("Download Excel"));
    await waitFor(() => expect(global.fetch).toHaveBeenCalled());
    expect(exportLineageToExcel).not.toHaveBeenCalled();
  });

  it("falls back to client export when server fails", async () => {
    (global.fetch as any).mockResolvedValue({ ok: false, status: 500, text: async () => "boom" });
    const user = userEvent.setup();
    render(<LineagePreview />);
    await user.click(screen.getByText("Download Excel"));
    await waitFor(() => expect(exportLineageToExcel).toHaveBeenCalled());
    expect(await screen.findByText(/generated the file locally/)).toBeInTheDocument();
  });

  it("shows failure note when client export also throws", async () => {
    (global.fetch as any).mockRejectedValue(new Error("network"));
    (exportLineageToExcel as any).mockImplementation(() => { throw new Error("nope"); });
    const user = userEvent.setup();
    render(<LineagePreview />);
    await user.click(screen.getByText("Download Excel"));
    expect(await screen.findByText(/Export failed/)).toBeInTheDocument();
  });

  it("closes on escape", () => {
    render(<LineagePreview />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useLineageStore.getState().previewOpen).toBe(false);
  });

  it("omits pipelines/columns tabs when empty and shows catalog scope", () => {
    seed({ nodes: [tbl("a")], columnEdges: [], scope: "catalog" });
    render(<LineagePreview />);
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
    expect(screen.queryByText("Columns")).not.toBeInTheDocument();
    expect(screen.getByText(/Catalog:/)).toBeInTheDocument();
  });
});
