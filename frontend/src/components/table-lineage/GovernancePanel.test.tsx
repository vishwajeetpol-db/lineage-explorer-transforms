import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GovernancePanel from "./GovernancePanel";
import { api, type GovernanceResponse, type GovernanceRule } from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("../../api/client", () => ({
  api: {
    getGovernance: vi.fn(),
    listGovernanceRules: vi.fn(),
    upsertGovernanceRule: vi.fn(),
    deleteGovernanceRule: vi.fn(),
  },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.schema.tbl";

const fullData: GovernanceResponse = {
  table_full_name: TABLE,
  owner: "alice@x.com",
  table_type: "MANAGED",
  created_by: "bob@x.com",
  created_at: "2026-01-01T00:00:00Z",
  last_altered_by: "carol@x.com",
  last_altered_at: "2026-06-01T00:00:00Z",
  comment: "a table comment",
  tags: [{ name: "domain", value: "sales" }, { name: "pii", value: null }],
  columns: [
    { name: "id", type: "int", nullable: "NO", comment: null, sensitivity: null, sensitivity_source: null },
    { name: "ssn", type: "string", nullable: "YES", comment: null, sensitivity: "PII", sensitivity_source: "tag" },
  ],
  sensitive_columns: [{ column: "ssn", sensitivity: "PII", source: "tag" }],
  config_rules_applied: 1,
  _cache: { from_cache: true, cached_at: new Date().toISOString(), cached_by: "me", stale: false },
};

const emptyData: GovernanceResponse = {
  ...fullData,
  comment: null,
  tags: [],
  columns: [],
  sensitive_columns: [],
  _cache: undefined,
};

const rules: GovernanceRule[] = [
  { rule_id: "r1", catalog: "cat", schema: "schema", table_pattern: "tbl", column_pattern: ".*", tag_name: null, sensitivity: "PII", owner: null, created_at: null, updated_at: null, notes: "whole table" },
  { rule_id: "r2", catalog: null, schema: null, table_pattern: null, column_pattern: null, tag_name: "pii", sensitivity: "PCI", owner: null, created_at: null, updated_at: null, notes: "tag pii=yes" },
  { rule_id: "r3", catalog: null, schema: null, table_pattern: null, column_pattern: "^ssn$", tag_name: null, sensitivity: "PII", owner: null, created_at: null, updated_at: null, notes: "column ssn" },
];

describe("GovernancePanel", () => {
  beforeEach(() => {
    (api.getGovernance as any).mockReset();
    (api.listGovernanceRules as any).mockReset().mockResolvedValue({ rules });
    (api.upsertGovernanceRule as any).mockReset().mockResolvedValue({ rule_id: "x", status: "ok" });
    (api.deleteGovernanceRule as any).mockReset().mockResolvedValue({ rule_id: "x", status: "ok" });
    useLineageStore.getState().setIsAdmin(false);
    useLineageStore.getState().reset();
  });

  it("renders NoTable when no table selected", () => {
    (api.getGovernance as any).mockResolvedValue(fullData);
    render(<GovernancePanel table={null} />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });

  it("shows loading state then data", async () => {
    let resolve: (v: GovernanceResponse) => void = () => {};
    (api.getGovernance as any).mockReturnValue(new Promise<GovernanceResponse>((r) => { resolve = r; }));
    render(<GovernancePanel table={TABLE} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    resolve(fullData);
    expect(await screen.findByText("a table comment")).toBeInTheDocument();
  });

  it("renders full data: identity, tags, classified columns, comment, rules", async () => {
    (api.getGovernance as any).mockResolvedValue(fullData);
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("a table comment")).toBeInTheDocument();
    expect(screen.getByText("alice@x.com")).toBeInTheDocument();
    expect(screen.getByText(/domain: sales/)).toBeInTheDocument();
    expect(screen.getByText("pii")).toBeInTheDocument(); // tag without value
    expect(screen.getAllByText("ssn").length).toBeGreaterThan(0);
    // rules rendered
    expect(await screen.findByText(/\(whole table\) → PII/)).toBeInTheDocument();
    expect(screen.getByText(/pii=yes → PCI/)).toBeInTheDocument();
    expect(screen.getByText(/\^ssn\$ → PII/)).toBeInTheDocument();
    // not admin => lock note, no remove buttons
    expect(screen.getByText(/Workspace admin required/)).toBeInTheDocument();
    expect(screen.queryByTitle("Remove rule")).not.toBeInTheDocument();
  });

  it("renders empty-ish data (no tags, no classified cols, no comment)", async () => {
    (api.getGovernance as any).mockResolvedValue(emptyData);
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("No Unity Catalog tags on this table.")).toBeInTheDocument();
    expect(screen.getByText("No tagged/classified columns.")).toBeInTheDocument();
  });

  it("renders true empty state when governance is null", async () => {
    (api.getGovernance as any).mockResolvedValue(null);
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("No data for this table.")).toBeInTheDocument();
  });

  it("shows error state when the api rejects", async () => {
    (api.getGovernance as any).mockRejectedValue(new Error("gov boom"));
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("gov boom")).toBeInTheDocument();
  });

  it("shows fallback error when rejection lacks a message", async () => {
    (api.getGovernance as any).mockRejectedValue({});
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("Failed to load governance")).toBeInTheDocument();
  });

  it("tolerates listGovernanceRules rejecting (empty rules)", async () => {
    (api.getGovernance as any).mockResolvedValue(fullData);
    (api.listGovernanceRules as any).mockRejectedValue(new Error("no rules"));
    render(<GovernancePanel table={TABLE} />);
    expect(await screen.findByText("a table comment")).toBeInTheDocument();
    expect(screen.queryByText(/\(whole table\) → PII/)).not.toBeInTheDocument();
  });

  it("refresh button re-calls getGovernance with refresh=true", async () => {
    (api.getGovernance as any).mockResolvedValue(fullData);
    const user = userEvent.setup();
    render(<GovernancePanel table={TABLE} />);
    await screen.findByText("a table comment");
    expect(api.getGovernance).toHaveBeenCalledTimes(1);
    await user.click(screen.getByRole("button", { name: /Refresh/ }));
    await waitFor(() => expect(api.getGovernance).toHaveBeenCalledTimes(2));
    expect(api.getGovernance).toHaveBeenLastCalledWith("cat", "schema", "tbl", true);
  });

  describe("as admin", () => {
    beforeEach(() => {
      useLineageStore.getState().setIsAdmin(true);
    });

    it("adds a column classification rule (whole table default)", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      const addBtns = screen.getAllByRole("button", { name: /Add/ });
      await user.click(addBtns[0]);
      await waitFor(() => expect(api.upsertGovernanceRule).toHaveBeenCalled());
      expect(api.upsertGovernanceRule).toHaveBeenCalledWith(expect.objectContaining({
        catalog: "cat", schema_name: "schema", table_pattern: "tbl", column_pattern: ".*", sensitivity: "PII",
      }));
    });

    it("adds a column rule for a specific column", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      // first select is the column-target select
      const selects = screen.getAllByRole("combobox");
      await user.selectOptions(selects[0], "ssn");
      const addBtns = screen.getAllByRole("button", { name: /Add/ });
      await user.click(addBtns[0]);
      await waitFor(() => expect(api.upsertGovernanceRule).toHaveBeenCalledWith(expect.objectContaining({
        column_pattern: "^ssn$", notes: "column ssn",
      })));
    });

    it("shows an error when adding a column rule fails", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      (api.upsertGovernanceRule as any).mockRejectedValue(new Error("add failed"));
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      await user.click(screen.getAllByRole("button", { name: /Add/ })[0]);
      expect(await screen.findByText("add failed")).toBeInTheDocument();
    });

    it("adds a tag rule with key and value", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      await user.type(screen.getByPlaceholderText(/tag key/), "pci");
      await user.type(screen.getByPlaceholderText(/value/), "true");
      await user.click(screen.getAllByRole("button", { name: /Add/ })[1]);
      await waitFor(() => expect(api.upsertGovernanceRule).toHaveBeenCalledWith(expect.objectContaining({
        tag_name: "pci", notes: "tag pci=true",
      })));
    });

    it("adds a tag rule with key only (any value)", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      await user.type(screen.getByPlaceholderText(/tag key/), "conf");
      await user.click(screen.getAllByRole("button", { name: /Add/ })[1]);
      await waitFor(() => expect(api.upsertGovernanceRule).toHaveBeenCalledWith(expect.objectContaining({
        tag_name: "conf", notes: "tag conf (any value)",
      })));
    });

    it("does not add a tag rule when the key is blank", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      // tag Add button disabled while key blank
      const addBtns = screen.getAllByRole("button", { name: /Add/ });
      expect(addBtns[1]).toBeDisabled();
      await user.click(addBtns[1]);
      expect(api.upsertGovernanceRule).not.toHaveBeenCalled();
    });

    it("shows an error when adding a tag rule fails", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      (api.upsertGovernanceRule as any).mockRejectedValue(new Error("tag failed"));
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      await user.type(screen.getByPlaceholderText(/tag key/), "pci");
      await user.click(screen.getAllByRole("button", { name: /Add/ })[1]);
      expect(await screen.findByText("tag failed")).toBeInTheDocument();
    });

    it("changes the sensitivity dropdowns before adding rules", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      const selects = screen.getAllByRole("combobox");
      // selects: [0]=column target, [1]=column sensitivity, [2]=tag sensitivity
      await user.selectOptions(selects[1], "PCI");
      await user.selectOptions(selects[2], "PHI");
      await user.click(screen.getAllByRole("button", { name: /Add/ })[0]);
      await waitFor(() => expect(api.upsertGovernanceRule).toHaveBeenCalledWith(expect.objectContaining({
        sensitivity: "PCI",
      })));
    });

    it("removes a rule", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      const removeBtns = await screen.findAllByTitle("Remove rule");
      await user.click(removeBtns[0]);
      await waitFor(() => expect(api.deleteGovernanceRule).toHaveBeenCalledWith("r1"));
    });

    it("shows an error when removing a rule fails", async () => {
      (api.getGovernance as any).mockResolvedValue(fullData);
      (api.deleteGovernanceRule as any).mockRejectedValue(new Error("del failed"));
      const user = userEvent.setup();
      render(<GovernancePanel table={TABLE} />);
      await screen.findByText("a table comment");
      const removeBtns = await screen.findAllByTitle("Remove rule");
      await user.click(removeBtns[0]);
      expect(await screen.findByText("del failed")).toBeInTheDocument();
    });
  });
});
