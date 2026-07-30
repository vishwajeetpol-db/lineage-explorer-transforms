import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PruningControls from "./PruningControls";
import { useTransformStore } from "../../store/transformStore";
import type { TransformResponse } from "../../api/transform";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

function traceWith(cats: string[]): TransformResponse {
  return {
    levels: [
      {
        depth: 0, label: "L0", color: "#fff",
        nodes: [],
        transforms: cats.map((c, i) => ({
          source_node_id: `s${i}`, target_node_id: `t${i}`, expression: "x", category: c, category_color: "#fff", source_file: "f",
        })),
      },
    ],
    has_lineage: true, is_source_column: false, cached: false, cached_at: null,
    fetch_duration_ms: 1, total_nodes: 1, total_edges: cats.length, max_depth_reached: 0,
  };
}

describe("PruningControls", () => {
  beforeEach(() => {
    useTransformStore.getState().reset();
  });

  it("renders nothing meaningful without a trace", () => {
    render(<PruningControls />);
    expect(screen.queryByText("ARITHMETIC")).not.toBeInTheDocument();
  });

  it("renders category chips from trace", () => {
    useTransformStore.setState({ traceResult: traceWith(["ARITHMETIC", "WINDOW"]) });
    render(<PruningControls />);
    expect(screen.getByText("ARITHMETIC")).toBeInTheDocument();
    expect(screen.getByText("WINDOW")).toBeInTheDocument();
  });

  it("toggles a category via chip click", async () => {
    useTransformStore.setState({ traceResult: traceWith(["ARITHMETIC"]) });
    const user = userEvent.setup();
    render(<PruningControls />);
    await user.click(screen.getByText("ARITHMETIC"));
    expect(useTransformStore.getState().hiddenCategories.has("ARITHMETIC")).toBe(true);
  });

  it("hides and shows all categories", async () => {
    useTransformStore.setState({ traceResult: traceWith(["ARITHMETIC", "JOIN"]) });
    const user = userEvent.setup();
    render(<PruningControls />);
    await user.click(screen.getByText("None"));
    expect(useTransformStore.getState().hiddenCategories.size).toBe(2);
    await user.click(screen.getByText("All"));
    expect(useTransformStore.getState().hiddenCategories.size).toBe(0);
  });

  it("shows path-isolated badge and clears isolation", async () => {
    useTransformStore.setState({ traceResult: traceWith(["JOIN"]), isolatedNodeId: "n1" });
    const user = userEvent.setup();
    render(<PruningControls />);
    expect(screen.getByText("Path isolated")).toBeInTheDocument();
    await user.click(screen.getByTitle("Clear path isolation"));
    expect(useTransformStore.getState().isolatedNodeId).toBeNull();
  });
});
