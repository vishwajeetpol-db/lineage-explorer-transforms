import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BiConsumersPanel } from "./BiConsumersPanel";

function mockFetch(data: any, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => data, text: async () => JSON.stringify(data) });
}

describe("BiConsumersPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders header and empty prompt initially", () => {
    render(<BiConsumersPanel />);
    expect(screen.getByText("BI Consumers")).toBeInTheDocument();
  });

  it("fetches and renders consumers", async () => {
    global.fetch = mockFetch({
      bi_consumers: [{ bi_tool: "Tableau", query_count: 1234, distinct_users: 5, last_accessed: "2024-05-01T10:00:00Z" }],
      lookback_days: 30,
    }) as any;
    const user = userEvent.setup();
    render(<BiConsumersPanel catalog="main" />);
    await user.click(screen.getByText("Detect Consumers"));
    expect(await screen.findByText("Tableau")).toBeInTheDocument();
    expect(screen.getByText("1,234")).toBeInTheDocument();
    expect(screen.getByText(/Last 30 days/)).toBeInTheDocument();
  });

  it("shows empty state after fetch with no consumers", async () => {
    global.fetch = mockFetch({ bi_consumers: [], lookback_days: 7 }) as any;
    const user = userEvent.setup();
    render(<BiConsumersPanel />);
    await user.click(screen.getByText("Detect Consumers"));
    expect(await screen.findByText(/No BI consumers detected/)).toBeInTheDocument();
  });

  it("shows unavailable error from payload", async () => {
    global.fetch = mockFetch({ available: false, error: "not enabled" }) as any;
    const user = userEvent.setup();
    render(<BiConsumersPanel />);
    await user.click(screen.getByText("Detect Consumers"));
    expect(await screen.findByText("not enabled")).toBeInTheDocument();
  });

  it("shows HTTP error", async () => {
    global.fetch = mockFetch({}, false, 500) as any;
    const user = userEvent.setup();
    render(<BiConsumersPanel />);
    await user.click(screen.getByText("Detect Consumers"));
    expect(await screen.findByText("HTTP 500")).toBeInTheDocument();
  });
});
