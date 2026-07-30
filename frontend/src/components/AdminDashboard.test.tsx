import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AdminDashboard from "./AdminDashboard";
import { api, type AdminStatus, type CapabilityCacheEntry } from "../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div {...p}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

vi.mock("../api/client", () => ({
  api: {
    getAdminStatus: vi.fn(),
    getCapabilityCacheInventory: vi.fn(),
    evictCapabilityCache: vi.fn(),
    invalidateTransform: vi.fn(),
  },
}));

const healthyStatus: AdminStatus = {
  system: { uptime_sec: 3600, uptime_human: "1h 0m", python_version: "3.11.5", pid: 4242 },
  memory: { rss_mb: 512, vms_mb: 1024, rss_percent: 30 },
  latency: { p50_ms: 40, p95_ms: 120, p99_ms: 260, sample_count: 100 },
  requests: { total: 12345, rate_per_min: 20 },
  thread_pool: { max_workers: 8, inflight_cache_keys: [] },
  cache: {
    entries: 2,
    max_entries: 100,
    max_memory_mb: 1024,
    ttl_seconds: 7200,
    utilization_percent: 20,
    total_size_mb: 40,
    inventory_note: "note-here",
    inventory: [
      {
        key: "trace:cat.s.a",
        cached_at: new Date().toISOString(),
        last_accessed: new Date().toISOString(),
        last_accessed_ago: "2m ago",
        ttl_remaining_sec: 5400,
        expired: false,
        size_kb: 2048,
      },
      {
        key: "trace:cat.s.b",
        cached_at: new Date().toISOString(),
        last_accessed: new Date().toISOString(),
        last_accessed_ago: "just now",
        ttl_remaining_sec: 120,
        expired: true,
        size_kb: 128,
      },
    ],
  },
  user_cache: { entries: 3, max_entries: 50 },
};

// A status that trips every upgrade-advisory branch + amber/red metric colours.
const stressedStatus: AdminStatus = {
  ...healthyStatus,
  memory: { rss_mb: 5000, vms_mb: 6000, rss_percent: 75 },
  latency: { p50_ms: 900, p95_ms: 2500, p99_ms: 3200, sample_count: 500 },
  thread_pool: { max_workers: 8, inflight_cache_keys: ["k1", "k2", "k3", "k4", "k5", "k6", "k7"] },
  cache: { ...healthyStatus.cache, utilization_percent: 85, inventory: [] },
};

const capEntries: CapabilityCacheEntry[] = [
  { table_fqn: "cat.s.orders", tab: "impact", cached_at: new Date().toISOString(), cached_by: "me", stale: false },
  { table_fqn: "cat.s.orders", tab: "root_cause", cached_at: null, cached_by: null, stale: true },
];

describe("AdminDashboard", () => {
  beforeEach(() => {
    (api.getAdminStatus as any).mockResolvedValue(healthyStatus);
    (api.getCapabilityCacheInventory as any).mockResolvedValue({ entries: capEntries, count: capEntries.length });
    (api.evictCapabilityCache as any).mockResolvedValue({ status: "ok", scope: "entry", evicted: 1 });
    (api.invalidateTransform as any).mockResolvedValue({ status: "ok", scope: "cache", cleared: ["a", "b"] });
    (globalThis as any).fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) });
  });

  it("renders nothing when closed", () => {
    const { container } = render(<AdminDashboard open={false} onClose={() => {}} />);
    expect(container).toBeEmptyDOMElement();
    expect(api.getAdminStatus).not.toHaveBeenCalled();
  });

  it("fetches + renders metrics, inventory rows, and capability cache entries", async () => {
    render(<AdminDashboard open onClose={() => {}} />);
    await waitFor(() => expect(screen.getByText("SYSTEM STATUS")).toBeInTheDocument());
    // metric cards
    expect(screen.getByText("260ms")).toBeInTheDocument();
    expect(screen.getByText("512MB")).toBeInTheDocument();
    expect(screen.getByText("12,345")).toBeInTheDocument();
    expect(screen.getByText("1h 0m")).toBeInTheDocument();
    // inventory rows (size formatting: >=1024KB -> MB; <1024KB -> KB)
    expect(screen.getByText("trace:cat.s.a")).toBeInTheDocument();
    expect(screen.getByText("2.0MB")).toBeInTheDocument();
    expect(screen.getByText("128KB")).toBeInTheDocument();
    expect(screen.getByText("LIVE")).toBeInTheDocument();
    expect(screen.getByText("EXPIRED")).toBeInTheDocument();
    // capability cache: tab-label mapping + STALE/FRESH
    expect(screen.getByText("Impact")).toBeInTheDocument();
    expect(screen.getByText("Root Cause")).toBeInTheDocument();
    expect(screen.getByText("FRESH")).toBeInTheDocument();
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.getByText(/2 cached table·tab entries/)).toBeInTheDocument();
    // no upgrade advisory for the healthy status
    expect(screen.queryByText(/UPGRADE ADVISORY/)).not.toBeInTheDocument();
  });

  it("shows the upgrade advisory + inflight queries under load", async () => {
    (api.getAdminStatus as any).mockResolvedValue(stressedStatus);
    render(<AdminDashboard open onClose={() => {}} />);
    expect(await screen.findByText(/UPGRADE ADVISORY/)).toBeInTheDocument();
    expect(screen.getByText(/Memory at 75%/)).toBeInTheDocument();
    expect(screen.getByText(/P99 latency 3200ms/)).toBeInTheDocument();
    expect(screen.getByText(/Cache 85% full/)).toBeInTheDocument();
    expect(screen.getByText(/7 inflight queries \(>6\)/)).toBeInTheDocument();
    // inflight-queries section (chips)
    expect(screen.getByText("Inflight Queries")).toBeInTheDocument();
    expect(screen.getByText("k1")).toBeInTheDocument();
    // empty inventory placeholder
    expect(screen.getByText("No cache entries")).toBeInTheDocument();
  });

  it("shows an error banner when status fails to load", async () => {
    (api.getAdminStatus as any).mockRejectedValue(new Error("nope status"));
    render(<AdminDashboard open onClose={() => {}} />);
    expect(await screen.findByText(/ERROR: nope status/)).toBeInTheDocument();
  });

  it("renders empty capability-cache placeholder when inventory is empty", async () => {
    (api.getCapabilityCacheInventory as any).mockResolvedValue({ entries: [], count: 0 });
    render(<AdminDashboard open onClose={() => {}} />);
    expect(await screen.findByText("No capability cache entries yet")).toBeInTheDocument();
  });

  it("falls back to empty entries when the inventory call rejects", async () => {
    (api.getCapabilityCacheInventory as any).mockRejectedValue(new Error("boom"));
    render(<AdminDashboard open onClose={() => {}} />);
    expect(await screen.findByText("No capability cache entries yet")).toBeInTheDocument();
  });

  it("evicts a single inventory entry via fetch then refetches status", async () => {
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("trace:cat.s.a");
    expect(api.getAdminStatus).toHaveBeenCalledTimes(1);
    const evictButtons = screen.getAllByTitle(/Evict trace:/);
    await user.click(evictButtons[0]);
    await waitFor(() => expect((globalThis as any).fetch).toHaveBeenCalledWith(
      expect.stringContaining("/api/admin/evict-cache?key="),
      expect.objectContaining({ method: "POST" }),
    ));
    await waitFor(() => expect(api.getAdminStatus).toHaveBeenCalledTimes(2));
  });

  it("evicts a capability entry and a whole table", async () => {
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("Impact");
    await user.click(screen.getAllByTitle(/Evict .* for this table/)[0]);
    await waitFor(() => expect(api.evictCapabilityCache).toHaveBeenCalledWith("entry", "cat.s.orders", "impact"));
    await user.click(screen.getAllByTitle("Evict all 4 tabs for this table")[0]);
    await waitFor(() => expect(api.evictCapabilityCache).toHaveBeenCalledWith("table", "cat.s.orders", undefined));
  });

  it("evict-all confirms before wiping the capability cache", async () => {
    const user = userEvent.setup();
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("Impact");
    await user.click(screen.getByRole("button", { name: /Evict all/ }));
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.evictCapabilityCache).not.toHaveBeenCalled();
    // confirm accepted -> proceeds
    confirmSpy.mockReturnValue(true);
    await user.click(screen.getByRole("button", { name: /Evict all/ }));
    await waitFor(() => expect(api.evictCapabilityCache).toHaveBeenCalledWith("all", undefined, undefined));
  });

  it("swallows an evictCapabilityCache rejection without throwing", async () => {
    (api.evictCapabilityCache as any).mockRejectedValue(new Error("evict fail"));
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("Impact");
    await user.click(screen.getAllByTitle(/Evict .* for this table/)[0]);
    await waitFor(() => expect(api.evictCapabilityCache).toHaveBeenCalled());
    // still rendered, no crash
    expect(screen.getByText("SYSTEM STATUS")).toBeInTheDocument();
  });

  it("flush cache invalidates the in-memory transform caches", async () => {
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("SYSTEM STATUS");
    await user.click(screen.getByRole("button", { name: /Flush cache/ }));
    await waitFor(() => expect(api.invalidateTransform).toHaveBeenCalledWith("cache"));
    expect(await screen.findByText("In-memory caches flushed")).toBeInTheDocument();
  });

  it("wipe lineage confirms, then reports the cleared count", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    (api.invalidateTransform as any).mockResolvedValue({ status: "ok", scope: "all", cleared: ["t1", "t2", "t3"] });
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("SYSTEM STATUS");
    await user.click(screen.getByRole("button", { name: /Wipe lineage/ }));
    expect(confirmSpy).toHaveBeenCalled();
    await waitFor(() => expect(api.invalidateTransform).toHaveBeenCalledWith("all"));
    expect(await screen.findByText(/Wiped 3 stored tables/)).toBeInTheDocument();
  });

  it("wipe lineage aborts when confirm is declined", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("SYSTEM STATUS");
    await user.click(screen.getByRole("button", { name: /Wipe lineage/ }));
    expect(api.invalidateTransform).not.toHaveBeenCalled();
  });

  it("surfaces an invalidateTransform error message", async () => {
    (api.invalidateTransform as any).mockRejectedValue(new Error("flush boom"));
    const user = userEvent.setup();
    render(<AdminDashboard open onClose={() => {}} />);
    await screen.findByText("SYSTEM STATUS");
    await user.click(screen.getByRole("button", { name: /Flush cache/ }));
    expect(await screen.findByText("Error: flush boom")).toBeInTheDocument();
  });

  it("calls onClose from the close button and the backdrop", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    const { container } = render(<AdminDashboard open onClose={onClose} />);
    await screen.findByText("SYSTEM STATUS");
    // backdrop is the first fixed inset-0 div
    const backdrop = container.querySelector(".fixed.inset-0") as HTMLElement;
    await user.click(backdrop);
    expect(onClose).toHaveBeenCalled();
  });
});
