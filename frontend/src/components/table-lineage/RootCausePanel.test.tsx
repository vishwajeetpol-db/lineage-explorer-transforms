import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RootCausePanel from "./RootCausePanel";
import { api, type RootCauseTrace } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getRootCauseTrace: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.schema.tbl";

const fullData: RootCauseTrace = {
  focus_table: TABLE,
  max_hops: 5,
  lookback_days: 30,
  stale_days: 7,
  counts: { failed: 1, stale: 1, healthy: 2, no_history: 0 },
  prime_suspect: {
    table: "cat.s.up",
    short_name: "s.up",
    hop: 2,
    is_focus: false,
    status: "failed",
    producers: [
      { entity_type: "JOB", entity_id: "1", status: "failed", last_result: "FAILED", last_run_at: "2026-07-01T10:00:00Z", success_rate: 0.5 },
      { entity_type: "PIPELINE", entity_id: "2", status: "stale", last_result: null, last_run_at: null, success_rate: null },
    ],
  },
  failure_path: [
    { table: "cat.s.up", short_name: "s.up", hop: 2, status: "failed" },
    { table: "cat.s.mid", short_name: "s.mid", hop: 1, status: "stale" },
    { table: TABLE, short_name: "s.tbl", hop: 0, status: "healthy" },
  ],
  flagged: [
    {
      table: "cat.s.up", short_name: "s.up", hop: 2, is_focus: false, status: "failed",
      producers: [{ entity_type: "JOB", entity_id: "1", status: "failed", last_result: "FAILED", last_run_at: "2026-07-01T10:00:00Z", success_rate: 0.5 }],
    },
    {
      table: TABLE, short_name: "s.tbl", hop: 0, is_focus: true, status: "healthy",
      producers: [{ entity_type: "JOB", entity_id: "9", status: "healthy", last_result: "SUCCESS", last_run_at: "2026-07-02T10:00:00Z", success_rate: 1 }],
    },
  ],
  _cache: { from_cache: true, cached_at: new Date().toISOString(), cached_by: "me", stale: false },
};

const emptyData: RootCauseTrace = {
  focus_table: TABLE,
  max_hops: 5,
  lookback_days: 30,
  stale_days: 7,
  counts: { failed: 0, stale: 0, healthy: 0, no_history: 0 },
  prime_suspect: null,
  failure_path: [],
  flagged: [],
};

describe("RootCausePanel", () => {
  beforeEach(() => {
    (api.getRootCauseTrace as any).mockReset();
  });

  it("renders NoTable when no table selected", () => {
    render(<RootCausePanel table={null} />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });

  it("shows loading state then data", async () => {
    let resolve: (v: RootCauseTrace) => void = () => {};
    (api.getRootCauseTrace as any).mockReturnValue(new Promise<RootCauseTrace>((r) => { resolve = r; }));
    render(<RootCausePanel table={TABLE} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    resolve(fullData);
    expect(await screen.findByText(/Prime suspect/)).toBeInTheDocument();
  });

  it("renders full data: prime suspect, failure path, flagged producers", async () => {
    (api.getRootCauseTrace as any).mockResolvedValue(fullData);
    render(<RootCausePanel table={TABLE} />);
    expect(await screen.findByText(/Prime suspect/)).toBeInTheDocument();
    // prime suspect hops-upstream label
    expect(screen.getByText("2 hops upstream")).toBeInTheDocument();
    // producer row rendered with success rate + entity
    expect(screen.getAllByText("50% ok").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/JOB 1/).length).toBeGreaterThan(0);
    // failure path section
    expect(screen.getByText("Failure path → focus")).toBeInTheDocument();
    // flagged producers count
    expect(screen.getByText("All flagged producers (2)")).toBeInTheDocument();
    // focus tag
    expect(screen.getByText("focus")).toBeInTheDocument();
  });

  it("renders empty state when trace has no data", async () => {
    (api.getRootCauseTrace as any).mockResolvedValue(emptyData);
    render(<RootCausePanel table={TABLE} />);
    // data exists but empty arrays -> the flagged header shows (0), no prime suspect block
    expect(await screen.findByText("All flagged producers (0)")).toBeInTheDocument();
    expect(screen.queryByText(/Prime suspect/)).not.toBeInTheDocument();
  });

  it("shows error state when the api rejects", async () => {
    (api.getRootCauseTrace as any).mockRejectedValue(new Error("trace failed"));
    render(<RootCausePanel table={TABLE} />);
    expect(await screen.findByText("trace failed")).toBeInTheDocument();
  });

  it("shows fallback error message when rejection has no message", async () => {
    (api.getRootCauseTrace as any).mockRejectedValue({});
    render(<RootCausePanel table={TABLE} />);
    expect(await screen.findByText("Failed to run trace")).toBeInTheDocument();
  });

  it("renders focus prime suspect with stale styling and singular hop label", async () => {
    const staleFocus: RootCauseTrace = {
      ...emptyData,
      counts: { failed: 0, stale: 1, healthy: 0, no_history: 1 },
      prime_suspect: {
        table: TABLE, short_name: "s.tbl", hop: 1, is_focus: true, status: "stale",
        producers: [{ entity_type: "JOB", entity_id: "1", status: "no_history", last_result: null, last_run_at: null, success_rate: null }],
      },
      failure_path: [{ table: TABLE, short_name: "s.tbl", hop: 0, status: "stale" }],
      flagged: [{
        table: TABLE, short_name: "s.tbl", hop: 1, is_focus: false, status: "stale",
        producers: [{ entity_type: "JOB", entity_id: "1", status: "no_history", last_result: null, last_run_at: null, success_rate: null }],
      }],
    };
    (api.getRootCauseTrace as any).mockResolvedValue(staleFocus);
    render(<RootCausePanel table={TABLE} />);
    // is_focus prime suspect => "focus table"
    expect(await screen.findByText("focus table")).toBeInTheDocument();
    // single-element failure path (no arrow) still renders
    expect(screen.getByText("Failure path → focus")).toBeInTheDocument();
    // non-focus flagged shows the +hop badge
    expect(screen.getByText("+1")).toBeInTheDocument();
  });

  it("renders a healthy prime suspect (default styling)", async () => {
    const healthy: RootCauseTrace = {
      ...emptyData,
      counts: { failed: 0, stale: 0, healthy: 1, no_history: 0 },
      prime_suspect: {
        table: TABLE, short_name: "s.tbl", hop: 3, is_focus: false, status: "healthy",
        producers: [{ entity_type: "JOB", entity_id: "1", status: "healthy", last_result: "SUCCESS", last_run_at: "2026-07-02T10:00:00Z", success_rate: 1 }],
      },
      failure_path: [],
      flagged: [],
    };
    (api.getRootCauseTrace as any).mockResolvedValue(healthy);
    render(<RootCausePanel table={TABLE} />);
    expect(await screen.findByText("3 hops upstream")).toBeInTheDocument();
    // no failure path section when empty
    expect(screen.queryByText("Failure path → focus")).not.toBeInTheDocument();
  });

  it("refresh button re-calls the api with refresh=true", async () => {
    (api.getRootCauseTrace as any).mockResolvedValue(fullData);
    const user = userEvent.setup();
    render(<RootCausePanel table={TABLE} />);
    await screen.findByText(/Prime suspect/);
    expect(api.getRootCauseTrace).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(api.getRootCauseTrace).toHaveBeenCalledTimes(2));
    expect(api.getRootCauseTrace).toHaveBeenLastCalledWith("cat", "schema", "tbl", undefined, true);
  });
});
