import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ImpactPanel from "./ImpactPanel";
import { api, type ImpactResponse } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getImpact: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.schema.tbl";

const fullData: ImpactResponse = {
  table_full_name: TABLE,
  max_hops: 5,
  lookback_days: 30,
  downstream_count: 3,
  consumer_owners: ["alice@x.com", "bob@x.com"],
  consumers: {
    by_type: { JOB: 2, DASHBOARD: 1 },
    total: 4,
    entities: [
      { entity_type: "JOB", entity_id: "123", display_name: "Nightly ETL", deep_link: "https://x/job/123" },
      { entity_type: "UNKNOWN_TYPE", entity_id: "999" },
    ],
  },
  sensitive_affected_count: 1,
  sensitive_affected: ["ssn"],
  downstream_tables: [
    { full_name: "cat.s.a", hop_distance: 1, owner: "alice@x.com", has_sensitive_columns: true },
    { full_name: "cat.s.b", hop_distance: 2, owner: null, has_sensitive_columns: false },
  ],
  _cache: { from_cache: true, cached_at: new Date().toISOString(), cached_by: "me", stale: false },
};

const emptyData: ImpactResponse = {
  table_full_name: TABLE,
  max_hops: 5,
  lookback_days: 30,
  downstream_count: 0,
  consumer_owners: [],
  sensitive_affected_count: 0,
  sensitive_affected: [],
  downstream_tables: [],
};

describe("ImpactPanel", () => {
  beforeEach(() => {
    (api.getImpact as any).mockReset();
  });

  it("renders NoTable when no table selected", () => {
    render(<ImpactPanel table={null} />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });

  it("shows loading state then data", async () => {
    let resolve: (v: ImpactResponse) => void = () => {};
    (api.getImpact as any).mockReturnValue(new Promise<ImpactResponse>((r) => { resolve = r; }));
    render(<ImpactPanel table={TABLE} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    resolve(fullData);
    expect(await screen.findByText("Nightly ETL")).toBeInTheDocument();
  });

  it("renders full data: stats, consumer types, entities, owners, downstream tables", async () => {
    (api.getImpact as any).mockResolvedValue(fullData);
    render(<ImpactPanel table={TABLE} />);
    expect(await screen.findByText("Nightly ETL")).toBeInTheDocument();
    // consumers by type
    expect(screen.getByText(/Jobs/)).toBeInTheDocument();
    expect(screen.getByText(/Dashboards/)).toBeInTheDocument();
    // unknown entity_type gets synthetic name
    expect(screen.getByText("UNKNOWN_TYPE 999")).toBeInTheDocument();
    // owners (alice appears both as affected owner and downstream table owner)
    expect(screen.getAllByText("alice@x.com").length).toBeGreaterThan(0);
    expect(screen.getByText("bob@x.com")).toBeInTheDocument();
    // downstream tables
    expect(screen.getByText("cat.s.a")).toBeInTheDocument();
    expect(screen.getByText("cat.s.b")).toBeInTheDocument();
    // deep-link is a link
    expect(screen.getByRole("link", { name: /Nightly ETL/ })).toHaveAttribute("href", "https://x/job/123");
  });

  it("shows the 'showing N of M' note when entities are truncated", async () => {
    (api.getImpact as any).mockResolvedValue(fullData);
    render(<ImpactPanel table={TABLE} />);
    expect(await screen.findByText(/Showing 2 of 4 consumers/)).toBeInTheDocument();
  });

  it("renders empty state when no downstream impact", async () => {
    (api.getImpact as any).mockResolvedValue(emptyData);
    render(<ImpactPanel table={TABLE} />);
    // empty label only fires when !data; here data exists but is empty -> renders zero stats
    expect(await screen.findByText("Downstream tables")).toBeInTheDocument();
    expect(screen.getByText("Downstream tables (0)")).toBeInTheDocument();
  });

  it("shows error state when the api rejects", async () => {
    (api.getImpact as any).mockRejectedValue(new Error("boom impact"));
    render(<ImpactPanel table={TABLE} />);
    expect(await screen.findByText("boom impact")).toBeInTheDocument();
  });

  it("shows a fallback error message when rejection has no message", async () => {
    (api.getImpact as any).mockRejectedValue({});
    render(<ImpactPanel table={TABLE} />);
    expect(await screen.findByText("Failed to load impact")).toBeInTheDocument();
  });

  it("refresh button re-calls the api with refresh=true", async () => {
    (api.getImpact as any).mockResolvedValue(fullData);
    const user = userEvent.setup();
    render(<ImpactPanel table={TABLE} />);
    await screen.findByText("Nightly ETL");
    expect(api.getImpact).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(api.getImpact).toHaveBeenCalledTimes(2));
    expect(api.getImpact).toHaveBeenLastCalledWith("cat", "schema", "tbl", undefined, true);
  });
});
