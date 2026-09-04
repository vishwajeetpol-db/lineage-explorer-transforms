import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TransformPanel from "./TransformPanel";
import { useTransformStore } from "../../store/transformStore";
import type { TransformResponse, FreshnessInfo, BuildJobStatus, CapturedExpression } from "../../api/transform";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div className={p.className} onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
vi.mock("./TransformCanvas", () => ({ default: () => <div data-testid="transform-canvas" /> }));

const triggerBuild = vi.fn();

function fresh(overrides: Partial<FreshnessInfo> = {}): FreshnessInfo {
  return { exists: false, edge_count: 0, last_built: null, age_str: "never", is_stale: false, ...overrides };
}
function trace(overrides: Partial<TransformResponse> = {}): TransformResponse {
  return {
    levels: [{ depth: 0, label: "Target", color: "#f00", nodes: [], transforms: [] }],
    has_lineage: true, is_source_column: false, cached: true, cached_at: null,
    fetch_duration_ms: 42, total_nodes: 3, total_edges: 2, max_depth_reached: 1, ...overrides,
  };
}

describe("TransformPanel", () => {
  beforeEach(() => {
    triggerBuild.mockReset();
    useTransformStore.getState().reset();
    useTransformStore.setState({ triggerBuild });
  });

  it("renders nothing when closed", () => {
    render(<TransformPanel />);
    expect(screen.queryByText("TRANSFORMATION LINEAGE")).not.toBeInTheDocument();
  });

  it("shows loading state", () => {
    useTransformStore.setState({ panelState: "loading", selectedTable: "c.s.t", selectedColumn: "col", freshness: fresh({ exists: true, is_stale: false }) });
    render(<TransformPanel />);
    expect(screen.getByText("Checking lineage freshness")).toBeInTheDocument();
    expect(screen.getByText(/FRESH/)).toBeInTheDocument();
  });

  it("shows needs_build state with generate button that triggers a build", async () => {
    useTransformStore.setState({ panelState: "needs_build", selectedTable: "c.s.t", selectedColumn: "col", freshness: fresh() });
    const user = userEvent.setup();
    render(<TransformPanel />);
    expect(screen.getByText("Transformation lineage not generated yet")).toBeInTheDocument();
    await user.click(screen.getByText("Generate transformation lineage"));
    expect(triggerBuild).toHaveBeenCalledWith("c.s.t", false);
  });

  it("shows stale needs_build with regenerate wording", () => {
    useTransformStore.setState({ panelState: "needs_build", selectedTable: "c.s.t", selectedColumn: "col", freshness: fresh({ exists: true, is_stale: true, edge_count: 4, age_str: "3d ago" }) });
    render(<TransformPanel />);
    expect(screen.getByText("Transformation lineage may be out of date")).toBeInTheDocument();
    expect(screen.getByText("Regenerate transformation lineage")).toBeInTheDocument();
  });

  it("shows building state", () => {
    const bs: BuildJobStatus = { run_id: "r", state: "RUNNING", result_state: null, state_message: "", progress_pct: 20, is_complete: false, is_success: false, current_step: 0, current_step_name: "Fetch", total_steps: 2, steps: ["Fetch", "Build"], run_page_url: "" };
    useTransformStore.setState({ panelState: "building", selectedTable: "c.s.t", selectedColumn: "col", buildStatus: bs });
    render(<TransformPanel />);
    expect(screen.getByText("Building Transformation Lineage")).toBeInTheDocument();
  });

  it("shows ready state with canvas and stats", () => {
    useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t", selectedColumn: "col", traceResult: trace() });
    render(<TransformPanel />);
    expect(screen.getByText("3 columns")).toBeInTheDocument();
    expect(screen.getByTestId("transform-canvas")).toBeInTheDocument();
    expect(screen.getByText("cached")).toBeInTheDocument();
  });

  it("shows source-column message when no upstream", () => {
    useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t", selectedColumn: "col", traceResult: trace({ is_source_column: true }) });
    render(<TransformPanel />);
    expect(screen.getByText("Source Column")).toBeInTheDocument();
  });

  it("shows no-lineage message", () => {
    useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t", selectedColumn: "col", traceResult: trace({ has_lineage: false }) });
    render(<TransformPanel />);
    expect(screen.getByText("No transformation logic found")).toBeInTheDocument();
  });

  it("shows error state and dismisses", async () => {
    const closePanel = vi.fn();
    useTransformStore.setState({ panelState: "error", selectedTable: "c.s.t", selectedColumn: "col", panelError: "bad", closePanel });
    const user = userEvent.setup();
    render(<TransformPanel />);
    expect(screen.getByText("bad")).toBeInTheDocument();
    await user.click(screen.getByText("Dismiss"));
    expect(closePanel).toHaveBeenCalled();
  });

  it("header build button rebuilds when already built", async () => {
    useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t", selectedColumn: "col", freshness: fresh({ exists: true, is_stale: false, age_str: "1h ago" }), traceResult: trace() });
    const user = userEvent.setup();
    render(<TransformPanel />);
    await user.click(screen.getByText("Lineage built"));
    expect(triggerBuild).toHaveBeenCalledWith("c.s.t", true);
  });

  it("renders runtime-captured expression card", () => {
    const ce: CapturedExpression = { target_column: "col", source_columns: ["a", "b"], expression: "a + b", confidence: 0.9, notes: "ok", captured_via: "plan", captured_at: "2024", version: 2 };
    useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t", selectedColumn: "col", traceResult: trace(), capturedExpression: ce });
    render(<TransformPanel />);
    expect(screen.getByText("Runtime-captured")).toBeInTheDocument();
    expect(screen.getByText("a + b")).toBeInTheDocument();
    expect(screen.getByText(/confidence 90%/)).toBeInTheDocument();
  });
});
