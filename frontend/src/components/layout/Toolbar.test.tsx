import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Toolbar from "./Toolbar";
import { useLineageStore } from "../../store/lineageStore";
import { api, setLiveMode } from "../../api/client";
import type { TableNode } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const nav = { goLanding: vi.fn(), goSchemas: vi.fn(), goCatalogs: vi.fn() };
vi.mock("../../hooks/useRouter", () => ({
  goLanding: () => nav.goLanding(),
  goSchemas: (c: string) => nav.goSchemas(c),
  goCatalogs: () => nav.goCatalogs(),
  goControlPanel: vi.fn(), goDQ: vi.fn(), goGlossary: vi.fn(), goNotifications: vi.fn(),
  goExport: vi.fn(), goRootCause: vi.fn(), goBiConsumers: vi.fn(), goStreaming: vi.fn(),
}));
vi.mock("../../api/client", () => ({
  api: { getCatalogs: vi.fn(), getSchemas: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));
vi.mock("../ui/ThemeToggle", () => ({ default: () => <div>theme</div> }));
vi.mock("./HeaderMenu", () => ({ default: () => <div>menu</div> }));

function tbl(name: string, status: TableNode["lineage_status"] = "connected"): TableNode {
  return {
    node_type: "table", id: name, name, full_name: `main.sales.${name}`, table_type: "MANAGED",
    owner: null, comment: null, columns: [], created_at: null, updated_at: null,
    upstream_count: 0, downstream_count: 0, lineage_status: status,
  };
}

function resetStore(overrides: any = {}) {
  useLineageStore.setState({
    catalog: "", schema: "", focusTable: null, scope: "table", lineageView: "full",
    lineageDepth: 0, columnLineageEnabled: false, liveMode: false, isAdmin: false,
    catalogs: ["main", "dev"], schemas: [], loading: false, cached: false, cachedAt: null,
    cacheExpiresAt: null, fetchDurationMs: null, lineageWindowDays: 90, discountPercent: 0,
    nodes: [], truncated: false, graphWarnings: null, healthWarning: null,
    searchOpen: false, previewOpen: false,
    ...overrides,
  });
}

describe("Toolbar", () => {
  beforeEach(() => {
    nav.goLanding.mockReset(); nav.goSchemas.mockReset(); nav.goCatalogs.mockReset();
    (api.getCatalogs as any).mockResolvedValue({ catalogs: ["main", "dev"] });
    (api.getSchemas as any).mockResolvedValue({ schemas: ["sales", "hr"] });
    (setLiveMode as any).mockReset();
    resetStore();
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads catalogs on mount and shows selectors", async () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    await waitFor(() => expect(api.getCatalogs).toHaveBeenCalled());
    expect(screen.getByText("Catalog")).toBeInTheDocument();
    expect(screen.getByText("Schema")).toBeInTheDocument();
  });

  it("selecting catalog + schema enables Generate", () => {
    const onGenerate = vi.fn();
    resetStore({ catalog: "main", schema: "sales" });
    render(<Toolbar onGenerate={onGenerate} />);
    fireEvent.click(screen.getByText("Generate Lineage"));
    expect(onGenerate).toHaveBeenCalled();
  });

  it("shows Loading... on the generate button while loading", () => {
    resetStore({ catalog: "main", schema: "sales", loading: true });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText("Loading...")).toBeInTheDocument();
  });

  it("changes catalog through the select box", () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    const selects = screen.getAllByRole("combobox");
    fireEvent.change(selects[0], { target: { value: "dev" } });
    expect(useLineageStore.getState().catalog).toBe("dev");
  });

  it("opens the view-mode dropdown and switches view", async () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Full"));
    fireEvent.click(screen.getByText("Pipelines"));
    expect(useLineageStore.getState().lineageView).toBe("pipeline");
  });

  it("toggles column lineage", async () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    const colToggle = screen.getByTitle("Column-level lineage").querySelector("button")!;
    fireEvent.click(colToggle);
    expect(useLineageStore.getState().columnLineageEnabled).toBe(true);
  });

  it("disables the live-mode toggle for non-admins", () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    const liveToggle = screen.getByTitle("Live mode — admins only").querySelector("button")!;
    expect(liveToggle).toBeDisabled();
    expect(useLineageStore.getState().liveMode).toBe(false);
  });

  it("enables live mode for admins and can dismiss the toast", () => {
    resetStore({ isAdmin: true });
    render(<Toolbar onGenerate={vi.fn()} />);
    const liveToggle = screen.getByTitle("Toggle live query mode").querySelector("button")!;
    fireEvent.click(liveToggle);
    expect(useLineageStore.getState().liveMode).toBe(true);
    expect(setLiveMode).toHaveBeenCalledWith(true);
    expect(screen.getByText(/Live mode enabled/)).toBeInTheDocument();
    // dismiss toast
    fireEvent.click(screen.getByText("×"));
    expect(screen.queryByText(/Live mode enabled/)).not.toBeInTheDocument();
  });

  it("disables live mode from an enabled state", () => {
    resetStore({ isAdmin: true, liveMode: true });
    render(<Toolbar onGenerate={vi.fn()} />);
    const liveToggle = screen.getByTitle("Toggle live query mode").querySelector("button")!;
    fireEvent.click(liveToggle);
    expect(useLineageStore.getState().liveMode).toBe(false);
    expect(screen.getByText(/Live mode disabled/)).toBeInTheDocument();
  });

  it("shows focused-table header with Back button", () => {
    resetStore({ focusTable: "main.sales.orders", catalog: "main", schema: "sales" });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText("main.sales.orders")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Back"));
    expect(nav.goLanding).toHaveBeenCalled();
  });

  it("shows schema-scope header and Back goes to schemas", () => {
    resetStore({ scope: "schema", catalog: "main", schema: "sales", nodes: [tbl("a")] });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText("main.sales")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Back"));
    expect(nav.goSchemas).toHaveBeenCalledWith("main");
  });

  it("shows catalog-scope header and Back goes to catalogs", () => {
    resetStore({ scope: "catalog", catalog: "main", nodes: [tbl("a")] });
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Back"));
    expect(nav.goCatalogs).toHaveBeenCalled();
  });

  it("opens the export preview when nodes exist", async () => {
    resetStore({ nodes: [tbl("a")], catalog: "main", schema: "sales" });
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Export"));
    expect(useLineageStore.getState().previewOpen).toBe(true);
  });

  it("opens search", async () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByTitle("Search (Cmd+K)"));
    expect(useLineageStore.getState().searchOpen).toBe(true);
  });

  it("opens the Options popover and edits discount + depth", async () => {
    resetStore({ nodes: [tbl("a")], focusTable: "main.sales.a", catalog: "main", schema: "sales" });
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Options"));
    const discount = screen.getByPlaceholderText("0");
    fireEvent.change(discount, { target: { value: "25" } });
    expect(useLineageStore.getState().discountPercent).toBe(25);
    const depth = screen.getByPlaceholderText("All");
    fireEvent.change(depth, { target: { value: "3" } });
    expect(useLineageStore.getState().lineageDepth).toBe(3);
  });

  it("renders cache/health/truncation/orphan/warning banners", () => {
    resetStore({
      nodes: [tbl("a"), tbl("orph", "orphan")],
      cached: true, cachedAt: new Date(Date.now() - 5 * 60000).toISOString(),
      cacheExpiresAt: new Date(Date.now() + 3600000).toISOString(),
      fetchDurationMs: 0,
      truncated: true,
      healthWarning: "system tables unavailable",
      graphWarnings: { C2: "missing edges" },
      lineageWindowDays: 90,
    });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText(/Cached/)).toBeInTheDocument();
    expect(screen.getByText("system tables unavailable")).toBeInTheDocument();
    expect(screen.getByText(/capped/)).toBeInTheDocument();
    expect(screen.getByText(/Graph may be incomplete/)).toBeInTheDocument();
    expect(screen.getByText(/no lineage in the last/)).toBeInTheDocument();
  });

  it("dismisses the graph-warnings banner", async () => {
    resetStore({ nodes: [tbl("a")], graphWarnings: { C2: "x" } });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText(/Graph may be incomplete/)).toBeInTheDocument();
    // The banner's dismiss button uses the × glyph
    const dismissBtns = screen.getAllByText("×");
    fireEvent.click(dismissBtns[dismissBtns.length - 1]);
    expect(screen.queryByText(/Graph may be incomplete/)).not.toBeInTheDocument();
  });

  it("shows live-mode cache banner text", () => {
    resetStore({ nodes: [tbl("a")], liveMode: true, isAdmin: true });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText(/LIVE MODE/)).toBeInTheDocument();
  });

  it("shows admin 'enable live mode' link in cached banner and toggles it", () => {
    resetStore({
      nodes: [tbl("a")], isAdmin: true, cached: true,
      cachedAt: new Date(Date.now() - 90 * 60000).toISOString(),
      cacheExpiresAt: new Date(Date.now() - 1000).toISOString(),
      fetchDurationMs: 4500,
    });
    render(<Toolbar onGenerate={vi.fn()} />);
    const link = screen.getByText(/Enable live mode for latest data/);
    fireEvent.click(link);
    expect(useLineageStore.getState().liveMode).toBe(true);
  });

  it("shows 'Loaded fresh' banner when not cached", () => {
    resetStore({
      nodes: [tbl("a")], cached: false,
      cacheExpiresAt: new Date(Date.now() + 25 * 3600000).toISOString(),
      fetchDurationMs: 1500,
    });
    render(<Toolbar onGenerate={vi.fn()} />);
    expect(screen.getByText(/Loaded fresh from system tables/)).toBeInTheDocument();
  });

  it("renders column toggle disabled in pipeline view", () => {
    resetStore({ lineageView: "pipeline", nodes: [tbl("a")] });
    render(<Toolbar onGenerate={vi.fn()} />);
    const wrap = screen.getByTitle("Column-level lineage");
    expect(wrap.className).toContain("pointer-events-none");
  });

  it("closes the view-mode dropdown on an outside mousedown", () => {
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Full"));
    expect(screen.getByText("Pipelines")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("Pipelines")).not.toBeInTheDocument();
  });

  it("closes the Options popover on an outside mousedown", () => {
    resetStore({ nodes: [tbl("a")], focusTable: "main.sales.a", catalog: "main", schema: "sales" });
    render(<Toolbar onGenerate={vi.fn()} />);
    fireEvent.click(screen.getByText("Options"));
    expect(screen.getByText("Depth")).toBeInTheDocument();
    fireEvent.mouseDown(document.body);
    expect(screen.queryByText("Depth")).not.toBeInTheDocument();
  });
});
