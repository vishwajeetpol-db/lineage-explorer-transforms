import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import TableLineageWorkspace from "./TableLineageWorkspace";
import { api, setLiveMode } from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

// The heavy graph layer + its provider are not unit-testable.
vi.mock("../graph/LineageCanvas", () => ({ default: () => <div data-testid="canvas" /> }));
vi.mock("reactflow", () => ({ ReactFlowProvider: ({ children }: any) => <div>{children}</div> }));

// Lightweight stubs for the capability panels — this suite covers the workspace
// shell (tabs, panels, trace loading), not each panel's internals.
vi.mock("./ImpactPanel", () => ({ default: ({ table }: any) => <div data-testid="impact-panel">{table}</div> }));
vi.mock("./RootCausePanel", () => ({ default: () => <div data-testid="rc-panel" /> }));
vi.mock("./GovernancePanel", () => ({ default: () => <div data-testid="gov-panel" /> }));
vi.mock("./AccessPanel", () => ({ default: () => <div data-testid="access-panel" /> }));
vi.mock("./MLModelsPanel", () => ({ default: () => <div data-testid="ml-panel" /> }));
vi.mock("./ColumnTransformationPanel", () => ({ default: () => <div data-testid="ct-panel" /> }));
vi.mock("./CatalogTreePanel", () => ({
  default: ({ onSelect }: any) => (
    <button data-testid="tree-select" onClick={() => onSelect("cat.sch.other")}>tree</button>
  ),
}));
vi.mock("./DraggablePanel", () => ({
  default: ({ title, children, onClose, onFocus }: any) => (
    <div data-testid="draggable-panel">
      <div>{title}</div>
      <button aria-label="focus-panel" onClick={onFocus}>focus</button>
      <button aria-label="close-panel" onClick={onClose}>close</button>
      {children}
    </div>
  ),
}));
vi.mock("../ui/ThemeToggle", () => ({ default: () => <div>theme</div> }));

const nav = { goLanding: vi.fn(), goTableLineage: vi.fn() };
vi.mock("../../hooks/useRouter", () => ({
  goLanding: () => nav.goLanding(),
  goTableLineage: (t?: string) => nav.goTableLineage(t),
}));

vi.mock("../../api/client", () => ({
  api: { getLineageTrace: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.sch.orders";

const traceResponse = {
  nodes: [
    {
      node_type: "table", id: TABLE, name: "orders", full_name: TABLE, table_type: "MANAGED_TABLE",
      owner: "alice", comment: "the orders table", columns: [{ name: "c1", type: "int", nullable: true }],
      created_at: null, updated_at: "2026-01-15T00:00:00Z", upstream_count: 2, downstream_count: 5,
      lineage_status: "connected",
    },
  ],
  edges: [],
  cached: true,
  cached_at: "2026-01-01T00:00:00Z",
  cache_expires_at: "2026-02-01T00:00:00Z",
  fetch_duration_ms: 12,
  lineage_window_days: 90,
  truncated: false,
  graph_warnings: null,
};

function resetStore(overrides: any = {}) {
  useLineageStore.setState({
    nodes: [], edges: [], columnEdges: [], loading: false, error: null,
    focusTable: null, liveMode: false, selectedNode: null,
    expandedNodes: new Set(), columnLineageEnabled: false,
    ...overrides,
  } as any);
}

beforeEach(() => {
  nav.goLanding.mockReset();
  nav.goTableLineage.mockReset();
  (api.getLineageTrace as any).mockResolvedValue(traceResponse);
  resetStore();
});

describe("TableLineageWorkspace", () => {
  it("shows the empty prompt when no table is selected", () => {
    render(<TableLineageWorkspace />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
    expect(screen.queryByTestId("canvas")).not.toBeInTheDocument();
  });

  it("loads a trace for the initial table and renders the summary bar + canvas", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalledWith(TABLE, expect.anything()));
    expect(screen.getByTestId("canvas")).toBeInTheDocument();
    // header shows the selected fqn
    expect(screen.getAllByText(new RegExp(TABLE)).length).toBeGreaterThan(0);
    // summary populated once the store has the focus node
    useLineageStore.getState().setLineageData({ nodes: traceResponse.nodes as any, edges: [] });
    await waitFor(() => expect(screen.getByText("the orders table")).toBeInTheDocument());
    // stats from the focus node
    expect(screen.getByText("Columns")).toBeInTheDocument();
    expect(screen.getByText("Upstream")).toBeInTheDocument();
    expect(screen.getByText("Downstream")).toBeInTheDocument();
    // sets live mode from store + column lineage enabled by loadTrace
    expect(setLiveMode).toHaveBeenCalled();
  });

  it("toggles capability panels open, brings to front, and closes them", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());

    // open Impact
    fireEvent.click(screen.getByRole("button", { name: /Impact/ }));
    expect(await screen.findByTestId("impact-panel")).toBeInTheDocument();

    // open Governance too (a second panel)
    fireEvent.click(screen.getByRole("button", { name: /Governance/ }));
    await waitFor(() => expect(screen.getAllByTestId("draggable-panel").length).toBe(2));

    // bring impact to front via its focus button
    const focusButtons = screen.getAllByLabelText("focus-panel");
    fireEvent.click(focusButtons[0]);

    // close a panel via its close button
    const closeButtons = screen.getAllByLabelText("close-panel");
    fireEvent.click(closeButtons[0]);
    await waitFor(() => expect(screen.getAllByTestId("draggable-panel").length).toBe(1));
  });

  it("clicking an open tab that is on top closes it", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());
    const impactBtn = screen.getByRole("button", { name: /Impact/ });
    fireEvent.click(impactBtn);
    expect(await screen.findByTestId("impact-panel")).toBeInTheDocument();
    // clicking again (it is on top) closes it
    fireEvent.click(impactBtn);
    await waitFor(() => expect(screen.queryByTestId("draggable-panel")).not.toBeInTheDocument());
  });

  it("re-opening a background tab brings it to front instead of closing", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());
    fireEvent.click(screen.getByRole("button", { name: /Impact/ }));
    fireEvent.click(screen.getByRole("button", { name: /Governance/ }));
    await waitFor(() => expect(screen.getAllByTestId("draggable-panel").length).toBe(2));
    // Impact is now in the background; clicking it brings to front, still 2 open
    fireEvent.click(screen.getByRole("button", { name: /Impact/ }));
    await waitFor(() => expect(screen.getAllByTestId("draggable-panel").length).toBe(2));
  });

  it("selecting a table from the tree navigates and reloads the trace", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalledWith(TABLE, expect.anything()));
    fireEvent.click(screen.getByTestId("tree-select"));
    await waitFor(() => expect(nav.goTableLineage).toHaveBeenCalledWith("cat.sch.other"));
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalledWith("cat.sch.other", expect.anything()));
  });

  it("refocuses when a table node is double-clicked in the graph (selectedNode)", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());
    // simulate the canvas setting a different valid 3-part table id
    useLineageStore.getState().setSelectedNode("cat.sch.dim");
    await waitFor(() => expect(nav.goTableLineage).toHaveBeenCalledWith("cat.sch.dim"));
  });

  it("ignores selectedNode values that aren't plain table fqns", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());
    nav.goTableLineage.mockClear();
    useLineageStore.getState().setSelectedNode("entity:JOB:1");
    // give effects a tick; should NOT navigate
    await new Promise((r) => setTimeout(r, 30));
    expect(nav.goTableLineage).not.toHaveBeenCalled();
  });

  it("records an error on the store when the trace fails", async () => {
    (api.getLineageTrace as any).mockRejectedValue(new Error("trace boom"));
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(useLineageStore.getState().error).toBe("trace boom"));
  });

  it("ignores an aborted trace without setting an error", async () => {
    (api.getLineageTrace as any).mockRejectedValue({ name: "AbortError" });
    render(<TableLineageWorkspace initialTable={TABLE} />);
    await waitFor(() => expect(api.getLineageTrace).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 30));
    expect(useLineageStore.getState().error).toBeNull();
  });

  it("Home button navigates to the landing page", async () => {
    render(<TableLineageWorkspace initialTable={TABLE} />);
    fireEvent.click(screen.getByRole("button", { name: /Home/ }));
    expect(nav.goLanding).toHaveBeenCalled();
  });
});
