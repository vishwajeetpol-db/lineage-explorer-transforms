import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import AccessPanel from "./AccessPanel";
import { api, type AccessResponse } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getAccess: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.schema.tbl";

const fullData: AccessResponse = {
  table_full_name: TABLE,
  lookback_days: 30,
  identities: {
    owner: "alice@x.com",
    created_by: "bob@x.com",
    created_at: "2026-01-01T00:00:00Z",
    last_altered_by: "carol@x.com",
    last_altered_at: "2026-06-01T00:00:00Z",
  },
  declared_grants: [
    { principal: "alice@x.com", privilege: "ALL_PRIVILEGES" },
    { principal: "alice@x.com", privilege: "MANAGE" },
    { principal: "grp_analysts", privilege: "SELECT" },
    { principal: "grp_etl", privilege: "MODIFY" },
    { principal: "", privilege: "SELECT" }, // skipped (no principal)
    { principal: "grp_noprivs" } as any, // principal but no privilege
  ],
  audit_access: [
    { user_email: "dave@x.com", action_name: "getTable", access_count: 12 },
    { user_email: "erin@x.com" },
  ],
  recent_events: [
    { event_time: "2026-07-01T10:00:00Z", action_name: "getTable", user_email: "dave@x.com", source_ip: "10.0.0.1" },
    { event_time: "2026-07-02T11:00:00Z", action_name: "commandSubmit", user_email: null, source_ip: null },
  ],
  dormant_grants: ["grp_dormant"],
  unique_empirical_users: 2,
  grantee_count: 4,
  read_count: 100,
  write_count: 3,
  _cache: { from_cache: true, cached_at: new Date().toISOString(), cached_by: "me", stale: false },
};

const emptyData: AccessResponse = {
  table_full_name: TABLE,
  lookback_days: 30,
  identities: { owner: null, created_by: null, created_at: null, last_altered_by: null, last_altered_at: null },
  declared_grants: [],
  audit_access: [],
  recent_events: [],
  dormant_grants: [],
  unique_empirical_users: 0,
  grantee_count: 0,
  read_count: 0,
  write_count: 0,
};

describe("AccessPanel", () => {
  beforeEach(() => {
    (api.getAccess as any).mockReset();
  });

  it("renders NoTable when no table selected", () => {
    render(<AccessPanel table={null} />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });

  it("shows loading state then data", async () => {
    let resolve: (v: AccessResponse) => void = () => {};
    (api.getAccess as any).mockReturnValue(new Promise<AccessResponse>((r) => { resolve = r; }));
    render(<AccessPanel table={TABLE} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    resolve(fullData);
    expect(await screen.findByText("grp_analysts")).toBeInTheDocument();
  });

  it("renders full data: stats, identities, grants, accessors, events, dormant", async () => {
    (api.getAccess as any).mockResolvedValue(fullData);
    render(<AccessPanel table={TABLE} />);
    expect(await screen.findByText("grp_analysts")).toBeInTheDocument();
    // identities (alice appears in identities + grants)
    expect(screen.getAllByText("alice@x.com").length).toBeGreaterThan(0);
    // grants grouped: alice has ALL_PRIVILEGES + MANAGE
    expect(screen.getByText("ALL_PRIVILEGES")).toBeInTheDocument();
    expect(screen.getByText("MANAGE")).toBeInTheDocument();
    expect(screen.getByText("SELECT")).toBeInTheDocument();
    expect(screen.getByText("MODIFY")).toBeInTheDocument();
    // grp_noprivs is grouped but shows no privileges
    expect(screen.getByText("grp_noprivs")).toBeInTheDocument();
    // accessors (dave appears in audit + events)
    expect(screen.getAllByText("dave@x.com").length).toBeGreaterThan(0);
    expect(screen.getByText("12×")).toBeInTheDocument();
    expect(screen.getByText("erin@x.com")).toBeInTheDocument();
    // recent events (getTable appears in both audit + events)
    expect(screen.getAllByText("getTable").length).toBeGreaterThan(0);
    expect(screen.getByText("commandSubmit")).toBeInTheDocument();
    expect(screen.getByText("—")).toBeInTheDocument(); // null user email
    // dormant grants
    expect(screen.getByText("grp_dormant")).toBeInTheDocument();
  });

  it("renders empty sections when everything is empty", async () => {
    (api.getAccess as any).mockResolvedValue(emptyData);
    render(<AccessPanel table={TABLE} />);
    expect(await screen.findByText("No declared grants visible.")).toBeInTheDocument();
    expect(screen.getByText("No audit access in window.")).toBeInTheDocument();
    // no identities section (all null)
    expect(screen.queryByText("Identities")).not.toBeInTheDocument();
    // no dormant / recent events sections
    expect(screen.queryByText(/Dormant grants/)).not.toBeInTheDocument();
    expect(screen.queryByText("Recent events")).not.toBeInTheDocument();
  });

  it("renders true empty state when access is null", async () => {
    (api.getAccess as any).mockResolvedValue(null);
    render(<AccessPanel table={TABLE} />);
    expect(await screen.findByText("No data for this table.")).toBeInTheDocument();
  });

  it("shows error state when the api rejects", async () => {
    (api.getAccess as any).mockRejectedValue(new Error("access boom"));
    render(<AccessPanel table={TABLE} />);
    expect(await screen.findByText("access boom")).toBeInTheDocument();
  });

  it("shows fallback error when rejection lacks a message", async () => {
    (api.getAccess as any).mockRejectedValue({});
    render(<AccessPanel table={TABLE} />);
    expect(await screen.findByText("Failed to load access")).toBeInTheDocument();
  });

  it("refresh button re-calls getAccess with refresh=true", async () => {
    (api.getAccess as any).mockResolvedValue(fullData);
    const user = userEvent.setup();
    render(<AccessPanel table={TABLE} />);
    await screen.findByText("grp_analysts");
    expect(api.getAccess).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(api.getAccess).toHaveBeenCalledTimes(2));
    expect(api.getAccess).toHaveBeenLastCalledWith("cat", "schema", "tbl", true);
  });
});
