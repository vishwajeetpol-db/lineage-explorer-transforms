import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SearchDialog from "./SearchDialog";
import { useLineageStore } from "../../store/lineageStore";
import type { TableNode } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

function tbl(name: string, table_type = "MANAGED"): TableNode {
  return {
    node_type: "table",
    id: name,
    name,
    full_name: `c.s.${name}`,
    table_type,
    owner: null,
    comment: null,
    columns: [],
    created_at: null,
    updated_at: null,
    upstream_count: 0,
    downstream_count: 0,
    lineage_status: "connected",
  };
}

describe("SearchDialog", () => {
  beforeEach(() => {
    useLineageStore.setState({
      searchOpen: true,
      searchQuery: "",
      nodes: [tbl("orders"), tbl("customers", "VIEW"), { ...tbl("job"), node_type: "entity" } as any],
    });
  });

  it("does not render content when closed", () => {
    useLineageStore.setState({ searchOpen: false });
    render(<SearchDialog onSelectNode={vi.fn()} />);
    expect(screen.queryByPlaceholderText("Search tables and views...")).not.toBeInTheDocument();
  });

  it("lists table nodes (excludes entities)", () => {
    render(<SearchDialog onSelectNode={vi.fn()} />);
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.queryByText("job")).not.toBeInTheDocument();
  });

  it("filters by query", () => {
    render(<SearchDialog onSelectNode={vi.fn()} />);
    const input = screen.getByPlaceholderText("Search tables and views...");
    fireEvent.change(input, { target: { value: "ord" } });
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.queryByText("customers")).not.toBeInTheDocument();
  });

  it("shows empty state when no tables match", () => {
    useLineageStore.setState({ searchQuery: "zzz" });
    render(<SearchDialog onSelectNode={vi.fn()} />);
    expect(screen.getByText("No tables found")).toBeInTheDocument();
  });

  it("selects a node and closes on click", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<SearchDialog onSelectNode={onSelect} />);
    await user.click(screen.getByText("orders"));
    expect(onSelect).toHaveBeenCalledWith("orders");
    expect(useLineageStore.getState().searchOpen).toBe(false);
  });

  it("toggles open with cmd+k and closes on escape", async () => {
    render(<SearchDialog onSelectNode={vi.fn()} />);
    await userEvent.keyboard("{Escape}");
    expect(useLineageStore.getState().searchOpen).toBe(false);
    await userEvent.keyboard("{Meta>}k{/Meta}");
    expect(useLineageStore.getState().searchOpen).toBe(true);
  });
});
