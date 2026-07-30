import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExportPanel } from "./ExportPanel";

function routeFetch(handlers: Record<string, any>) {
  return vi.fn().mockImplementation((url: string) => {
    for (const key of Object.keys(handlers)) {
      if (url.includes(key)) return Promise.resolve({ ok: true, json: async () => handlers[key] });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

describe("ExportPanel", () => {
  beforeEach(() => {
    (URL as any).createObjectURL = vi.fn(() => "blob:x");
    (URL as any).revokeObjectURL = vi.fn();
    vi.spyOn(window, "alert").mockImplementation(() => {});
    // jsdom anchor click is a noop; ensure it doesn't throw
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders export tab by default", () => {
    render(<ExportPanel catalog="main" schema="s" />);
    expect(screen.getByText("Export & Interop")).toBeInTheDocument();
    expect(screen.getByText("Export as OpenLineage JSON")).toBeInTheDocument();
  });

  it("exports openlineage json", async () => {
    const fetchMock = routeFetch({ "export/openlineage": { events: [] } });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<ExportPanel catalog="main" schema="s" />);
    await user.click(screen.getByText("Export as OpenLineage JSON"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("export/openlineage"))).toBe(true);
    });
    expect((URL as any).createObjectURL).toHaveBeenCalled();
  });

  it("imports events and shows result", async () => {
    global.fetch = routeFetch({ "import/openlineage": { imported: 2 } }) as any;
    const user = userEvent.setup();
    render(<ExportPanel catalog="main" />);
    await user.click(screen.getByText("Import Events"));
    // textarea empty -> import button disabled; type valid JSON
    const textarea = screen.getByPlaceholderText(/eventType/);
    await user.type(textarea, '{{"eventType":"COMPLETE"}');
    await user.click(screen.getAllByText("Import Events")[1] ?? screen.getByText("Import Events"));
    expect(await screen.findByText(/imported/)).toBeInTheDocument();
  });

  it("shows import parse error", async () => {
    global.fetch = routeFetch({}) as any;
    const user = userEvent.setup();
    render(<ExportPanel catalog="main" />);
    await user.click(screen.getByText("Import Events"));
    const textarea = screen.getByPlaceholderText(/eventType/);
    await user.type(textarea, "not json");
    await user.click(screen.getAllByText("Import Events")[1] ?? screen.getByText("Import Events"));
    expect(await screen.findByText(/error/)).toBeInTheDocument();
  });

  it("loads snapshots tab and shows empty state", async () => {
    global.fetch = routeFetch({ "/api/snapshots": { snapshots: [] } }) as any;
    const user = userEvent.setup();
    render(<ExportPanel catalog="main" />);
    await user.click(screen.getByText("Graph Snapshots"));
    expect(await screen.findByText(/No snapshots yet/)).toBeInTheDocument();
  });

  it("captures a snapshot", async () => {
    const fetchMock = routeFetch({
      "snapshots/capture": { node_count: 10, edge_count: 8 },
      "/api/snapshots": { snapshots: [{ snapshot_id: "abcdef1234", label: "snap", scope: "main", captured_at: "2024-05-01T00:00:00Z", node_count: 10, edge_count: 8 }] },
    });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<ExportPanel catalog="main" />);
    await user.click(screen.getByText("Graph Snapshots"));
    await user.click(screen.getByText("Capture Now"));
    expect(await screen.findByText("snap")).toBeInTheDocument();
    expect(window.alert).toHaveBeenCalled();
  });
});
