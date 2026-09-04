import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StreamingTopologyPanel } from "./StreamingTopologyPanel";

function mockFetch(data: any, ok = true, status = 200) {
  return vi.fn().mockResolvedValue({ ok, status, json: async () => data });
}

describe("StreamingTopologyPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders header and initial prompt", () => {
    render(<StreamingTopologyPanel />);
    expect(screen.getByText("Streaming Topology")).toBeInTheDocument();
    expect(screen.getByText(/Click "Detect Topology"/)).toBeInTheDocument();
  });

  it("renders nodes and edges after fetch", async () => {
    global.fetch = mockFetch({
      streaming_tables: [{ table_catalog: "main", table_schema: "s", table_name: "sink", data_source_format: "delta" }],
      streaming_edges: [{ source: "main.s.src", target: "main.s.sink", relationship: "stream" }],
    }) as any;
    const user = userEvent.setup();
    render(<StreamingTopologyPanel catalog="main" />);
    await user.click(screen.getByText("Detect Topology"));
    expect(await screen.findByText("main.s.src")).toBeInTheDocument();
    expect(screen.getAllByText("main.s.sink").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Streaming Tables")).toBeInTheDocument();
  });

  it("shows unavailable error", async () => {
    global.fetch = mockFetch({ available: false, error: "no streaming" }) as any;
    const user = userEvent.setup();
    render(<StreamingTopologyPanel />);
    await user.click(screen.getByText("Detect Topology"));
    expect(await screen.findByText("no streaming")).toBeInTheDocument();
  });

  it("shows HTTP error", async () => {
    global.fetch = mockFetch({}, false, 502) as any;
    const user = userEvent.setup();
    render(<StreamingTopologyPanel />);
    await user.click(screen.getByText("Detect Topology"));
    expect(await screen.findByText("HTTP 502")).toBeInTheDocument();
  });
});
