import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { useTransformStore } from "./transformStore";
import * as api from "../api/transform";
import type {
  FreshnessInfo,
  BuildJobStatus,
  TransformResponse,
  CapturedExpression,
} from "../api/transform";

vi.mock("../api/transform", () => ({
  getTransformFreshness: vi.fn(),
  getTransformTrace: vi.fn(),
  submitTransformBuild: vi.fn(),
  getBuildStatus: vi.fn(),
  getTransformCategories: vi.fn(),
  getCapturedExpression: vi.fn(),
}));

const mocked = api as unknown as {
  getTransformFreshness: ReturnType<typeof vi.fn>;
  getTransformTrace: ReturnType<typeof vi.fn>;
  submitTransformBuild: ReturnType<typeof vi.fn>;
  getBuildStatus: ReturnType<typeof vi.fn>;
  getTransformCategories: ReturnType<typeof vi.fn>;
  getCapturedExpression: ReturnType<typeof vi.fn>;
};

const get = () => useTransformStore.getState();

const fresh = (over: Partial<FreshnessInfo> = {}): FreshnessInfo => ({
  exists: true,
  edge_count: 1,
  last_built: "t",
  age_str: "1h",
  is_stale: false,
  ...over,
});

const trace = (over: Partial<TransformResponse> = {}): TransformResponse => ({
  levels: [],
  has_lineage: true,
  is_source_column: false,
  cached: false,
  cached_at: null,
  fetch_duration_ms: null,
  total_nodes: 0,
  total_edges: 0,
  max_depth_reached: 0,
  ...over,
});

const captured: CapturedExpression = {
  target_column: "c",
  source_columns: ["a"],
  expression: "a+1",
  confidence: 0.9,
  notes: "",
  captured_via: null,
  captured_at: null,
  version: null,
};

const buildStatus = (over: Partial<BuildJobStatus> = {}): BuildJobStatus => ({
  run_id: "r1",
  state: "RUNNING",
  result_state: null,
  state_message: "",
  progress_pct: 0,
  is_complete: false,
  is_success: false,
  current_step: 0,
  current_step_name: "",
  total_steps: 3,
  steps: [],
  run_page_url: "",
  ...over,
});

describe("transformStore", () => {
  beforeEach(() => {
    get().reset();
    mocked.getTransformFreshness.mockReset();
    mocked.getTransformTrace.mockReset();
    mocked.submitTransformBuild.mockReset();
    mocked.getBuildStatus.mockReset();
    mocked.getTransformCategories.mockReset();
    mocked.getCapturedExpression.mockReset();
    mocked.getCapturedExpression.mockResolvedValue(captured);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("has expected initial state", () => {
    expect(get().panelState).toBe("closed");
    expect(get().maxDepth).toBe(8);
    expect(get().hiddenCategories.size).toBe(0);
  });

  describe("openPanel", () => {
    it("errors on invalid table fqn", async () => {
      await get().openPanel("bad", "col");
      expect(get().panelState).toBe("error");
      expect(get().panelError).toBe("Invalid table name");
    });

    it("goes to needs_build when lineage does not exist", async () => {
      mocked.getTransformFreshness.mockResolvedValue(fresh({ exists: false }));
      await get().openPanel("c.s.t", "col");
      expect(get().panelState).toBe("needs_build");
      expect(get().selectedTable).toBe("c.s.t");
      expect(get().freshness?.exists).toBe(false);
    });

    it("goes to needs_build when stale", async () => {
      mocked.getTransformFreshness.mockResolvedValue(fresh({ is_stale: true }));
      await get().openPanel("c.s.t", "col");
      expect(get().panelState).toBe("needs_build");
    });

    it("loads trace when fresh and existing", async () => {
      mocked.getTransformFreshness.mockResolvedValue(fresh());
      mocked.getTransformTrace.mockResolvedValue(trace());
      await get().openPanel("c.s.t", "col");
      expect(get().panelState).toBe("ready");
      expect(get().traceResult).not.toBeNull();
    });

    it("aborts (no state change) if selection changed during freshness fetch", async () => {
      mocked.getTransformFreshness.mockImplementation(async () => {
        // Simulate the user clicking a different column mid-flight
        useTransformStore.setState({ selectedColumn: "other" });
        return fresh();
      });
      await get().openPanel("c.s.t", "col");
      // Because guard tripped, trace should never have been loaded
      expect(mocked.getTransformTrace).not.toHaveBeenCalled();
    });

    it("sets error when freshness throws (and panel still active)", async () => {
      mocked.getTransformFreshness.mockRejectedValue(new Error("net down"));
      await get().openPanel("c.s.t", "col");
      expect(get().panelState).toBe("error");
      expect(get().panelError).toBe("net down");
    });
  });

  describe("closePanel / reset", () => {
    it("closePanel restores initial state", async () => {
      useTransformStore.setState({ panelState: "ready", selectedTable: "c.s.t" });
      get().closePanel();
      expect(get().panelState).toBe("closed");
      expect(get().selectedTable).toBeNull();
    });
  });

  describe("checkFreshness", () => {
    it("returns freshness and clears loading", async () => {
      mocked.getTransformFreshness.mockResolvedValue(fresh());
      const result = await get().checkFreshness("c", "s", "t");
      expect(result.exists).toBe(true);
      expect(get().freshnessLoading).toBe(false);
    });

    it("rethrows and clears loading on error", async () => {
      mocked.getTransformFreshness.mockRejectedValue(new Error("x"));
      await expect(get().checkFreshness("c", "s", "t")).rejects.toThrow("x");
      expect(get().freshnessLoading).toBe(false);
    });
  });

  describe("triggerBuild", () => {
    it("submitted → begins polling and completes successfully", async () => {
      useTransformStore.setState({ selectedTable: "c.s.t", selectedColumn: "col" });
      mocked.submitTransformBuild.mockResolvedValue({ status: "submitted", run_id: "r1" });
      mocked.getBuildStatus.mockResolvedValue(
        buildStatus({ is_complete: true, is_success: true }),
      );
      mocked.getTransformTrace.mockResolvedValue(trace());
      await get().triggerBuild("c.s.t");
      // pollBuild runs synchronously to completion in the immediate path
      await vi.waitFor(() => expect(get().panelState).toBe("ready"));
      expect(get().buildPolling).toBe(false);
    });

    it("fresh status loads trace directly", async () => {
      useTransformStore.setState({ selectedColumn: "col" });
      mocked.submitTransformBuild.mockResolvedValue({ status: "fresh" });
      mocked.getTransformTrace.mockResolvedValue(trace());
      await get().triggerBuild("c.s.t");
      expect(mocked.getTransformTrace).toHaveBeenCalled();
      expect(get().panelState).toBe("ready");
    });

    it("skipped status loads trace directly", async () => {
      useTransformStore.setState({ selectedColumn: "col" });
      mocked.submitTransformBuild.mockResolvedValue({ status: "skipped" });
      mocked.getTransformTrace.mockResolvedValue(trace());
      await get().triggerBuild("c.s.t");
      expect(mocked.getTransformTrace).toHaveBeenCalled();
    });

    it("unknown status → error", async () => {
      mocked.submitTransformBuild.mockResolvedValue({ status: "weird" as any, message: "huh" });
      await get().triggerBuild("c.s.t");
      expect(get().panelState).toBe("error");
      expect(get().panelError).toBe("huh");
    });

    it("submit throws → error", async () => {
      mocked.submitTransformBuild.mockRejectedValue(new Error("submit fail"));
      await get().triggerBuild("c.s.t");
      expect(get().panelState).toBe("error");
      expect(get().panelError).toBe("submit fail");
    });
  });

  describe("pollBuild", () => {
    it("returns immediately with no run id", async () => {
      useTransformStore.setState({ buildRunId: null });
      await get().pollBuild();
      expect(mocked.getBuildStatus).not.toHaveBeenCalled();
    });

    it("aborts before fetch if polling stopped", async () => {
      useTransformStore.setState({ buildRunId: "r1", buildPolling: false });
      await get().pollBuild();
      expect(mocked.getBuildStatus).not.toHaveBeenCalled();
    });

    it("build failure sets error state", async () => {
      useTransformStore.setState({
        buildRunId: "r1",
        buildPolling: true,
        panelState: "building",
      });
      mocked.getBuildStatus.mockResolvedValue(
        buildStatus({ is_complete: true, is_success: false, result_state: "FAILED" }),
      );
      await get().pollBuild();
      await vi.waitFor(() => expect(get().panelState).toBe("error"));
      expect(get().panelError).toContain("Build failed");
    });

    it("schedules another tick while still running, then completes", async () => {
      vi.useFakeTimers();
      useTransformStore.setState({
        buildRunId: "r1",
        buildPolling: true,
        panelState: "building",
        selectedTable: "c.s.t",
        selectedColumn: "col",
      });
      mocked.getBuildStatus
        .mockResolvedValueOnce(buildStatus({ is_complete: false }))
        .mockResolvedValueOnce(buildStatus({ is_complete: true, is_success: true }));
      mocked.getTransformTrace.mockResolvedValue(trace());

      await get().pollBuild();
      expect(mocked.getBuildStatus).toHaveBeenCalledTimes(1);
      // advance the 3s setTimeout
      await vi.advanceTimersByTimeAsync(3000);
      expect(mocked.getBuildStatus).toHaveBeenCalledTimes(2);
      expect(get().buildPolling).toBe(false);
    });

    it("polling error sets error state when panel open", async () => {
      useTransformStore.setState({
        buildRunId: "r1",
        buildPolling: true,
        panelState: "building",
      });
      mocked.getBuildStatus.mockRejectedValue(new Error("poll boom"));
      await get().pollBuild();
      await vi.waitFor(() => expect(get().panelState).toBe("error"));
      expect(get().panelError).toContain("poll boom");
      expect(get().buildPolling).toBe(false);
    });

    it("aborts after fetch if panel was closed during await", async () => {
      useTransformStore.setState({
        buildRunId: "r1",
        buildPolling: true,
        panelState: "building",
      });
      mocked.getBuildStatus.mockImplementation(async () => {
        useTransformStore.setState({ panelState: "closed" });
        return buildStatus({ is_complete: true, is_success: true });
      });
      await get().pollBuild();
      // panelState stays closed, no error transition
      expect(get().panelState).toBe("closed");
    });
  });

  describe("loadTrace", () => {
    it("sets traceResult + ready on success", async () => {
      mocked.getTransformTrace.mockResolvedValue(trace({ total_nodes: 5 }));
      await get().loadTrace("c", "s", "t", "col");
      expect(get().traceResult?.total_nodes).toBe(5);
      expect(get().panelState).toBe("ready");
    });

    it("uses provided depth override", async () => {
      mocked.getTransformTrace.mockResolvedValue(trace());
      await get().loadTrace("c", "s", "t", "col", 3);
      expect(mocked.getTransformTrace).toHaveBeenCalledWith("c", "s", "t", "col", 3);
    });

    it("error → error state", async () => {
      mocked.getTransformTrace.mockRejectedValue(new Error("trace fail"));
      await get().loadTrace("c", "s", "t", "col");
      expect(get().panelState).toBe("error");
      expect(get().panelError).toBe("trace fail");
    });
  });

  describe("loadCapturedExpression", () => {
    it("sets expression when active selection matches", async () => {
      useTransformStore.setState({ selectedTable: "c.s.t", selectedColumn: "col", panelState: "ready" });
      mocked.getCapturedExpression.mockResolvedValue(captured);
      await get().loadCapturedExpression("c", "s", "t", "col");
      expect(get().capturedExpression).toEqual(captured);
      expect(get().capturedExpressionLoading).toBe(false);
    });

    it("ignores result when selection changed", async () => {
      useTransformStore.setState({ selectedTable: "other.s.t", selectedColumn: "x" });
      mocked.getCapturedExpression.mockResolvedValue(captured);
      await get().loadCapturedExpression("c", "s", "t", "col");
      expect(get().capturedExpression).toBeNull();
    });

    it("swallows errors and clears when active", async () => {
      useTransformStore.setState({ selectedTable: "c.s.t", selectedColumn: "col", panelState: "ready" });
      mocked.getCapturedExpression.mockRejectedValue(new Error("nope"));
      await get().loadCapturedExpression("c", "s", "t", "col");
      expect(get().capturedExpression).toBeNull();
      expect(get().capturedExpressionLoading).toBe(false);
    });

    it("swallows errors silently when selection changed", async () => {
      useTransformStore.setState({ selectedTable: "diff.s.t", selectedColumn: "col" });
      mocked.getCapturedExpression.mockRejectedValue(new Error("nope"));
      await get().loadCapturedExpression("c", "s", "t", "col");
      // no throw; capturedExpressionLoading remains true (set before await)
      expect(get().capturedExpression).toBeNull();
    });
  });

  describe("loadCategories", () => {
    it("sets categories on success", async () => {
      mocked.getTransformCategories.mockResolvedValue({
        categories: { agg: "#fff" },
        level_colors: [],
      });
      await get().loadCategories();
      expect(get().categories).toEqual({ agg: "#fff" });
    });

    it("swallows errors", async () => {
      mocked.getTransformCategories.mockRejectedValue(new Error("x"));
      await get().loadCategories();
      expect(get().categories).toEqual({});
    });
  });

  describe("pruning actions", () => {
    it("setMaxDepth clamps and refetches when selection present", async () => {
      useTransformStore.setState({ selectedTable: "c.s.t", selectedColumn: "col" });
      mocked.getTransformTrace.mockResolvedValue(trace());
      get().setMaxDepth(20);
      expect(get().maxDepth).toBe(8);
      expect(mocked.getTransformTrace).toHaveBeenCalledWith("c", "s", "t", "col", 8);
    });

    it("setMaxDepth clamps low and does not refetch without selection", () => {
      useTransformStore.setState({ selectedTable: null, selectedColumn: null });
      get().setMaxDepth(0);
      expect(get().maxDepth).toBe(1);
      expect(mocked.getTransformTrace).not.toHaveBeenCalled();
    });

    it("setMaxDepth ignores refetch when fqn incomplete", () => {
      useTransformStore.setState({ selectedTable: "bad", selectedColumn: "col" });
      get().setMaxDepth(4);
      expect(get().maxDepth).toBe(4);
      expect(mocked.getTransformTrace).not.toHaveBeenCalled();
    });

    it("toggleCategory adds then removes", () => {
      get().toggleCategory("agg");
      expect(get().hiddenCategories.has("agg")).toBe(true);
      get().toggleCategory("agg");
      expect(get().hiddenCategories.has("agg")).toBe(false);
    });

    it("showAllCategories clears the set", () => {
      get().toggleCategory("agg");
      get().showAllCategories();
      expect(get().hiddenCategories.size).toBe(0);
    });

    it("hideAllCategories with no trace is a no-op", () => {
      useTransformStore.setState({ traceResult: null });
      get().hideAllCategories();
      expect(get().hiddenCategories.size).toBe(0);
    });

    it("hideAllCategories gathers all categories from the trace", () => {
      useTransformStore.setState({
        traceResult: trace({
          levels: [
            {
              depth: 1,
              label: "l",
              color: "#000",
              nodes: [],
              transforms: [
                { source_node_id: "a", target_node_id: "b", expression: "e", category: "agg", category_color: "#f", source_file: "f" },
                { source_node_id: "b", target_node_id: "c", expression: "e", category: "join", category_color: "#f", source_file: "f" },
              ],
            },
          ],
        }),
      });
      get().hideAllCategories();
      expect(get().hiddenCategories).toEqual(new Set(["agg", "join"]));
    });

    it("isolateNode / clearIsolation", () => {
      get().isolateNode("n1");
      expect(get().isolatedNodeId).toBe("n1");
      get().clearIsolation();
      expect(get().isolatedNodeId).toBeNull();
    });
  });
});
