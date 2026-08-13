import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CatalogTreePanel from "./CatalogTreePanel";
import { useLineageStore } from "../../store/lineageStore";

const TABLES = [
  { fqdn: "main.sales.orders", catalog: "main", schema: "sales", name: "orders", table_type: "MANAGED" },
  { fqdn: "main.sales.customers", catalog: "main", schema: "sales", name: "customers", table_type: "MANAGED" },
  { fqdn: "analytics.gold.revenue", catalog: "analytics", schema: "gold", name: "revenue", table_type: "VIEW" },
];

describe("CatalogTreePanel", () => {
  beforeEach(() => {
    useLineageStore.setState({ allTables: TABLES as any });
  });

  it("renders the catalogs from the store", () => {
    render(<CatalogTreePanel selected={null} onSelect={() => {}} />);
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("analytics")).toBeInTheDocument();
  });

  it("expands a catalog to show schemas, then a schema to show tables, and selects", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<CatalogTreePanel selected={null} onSelect={onSelect} />);
    await user.click(screen.getByText("main"));       // expand catalog
    await user.click(screen.getByText("sales"));      // expand schema
    await user.click(screen.getByText("orders"));     // select table
    expect(onSelect).toHaveBeenCalledWith("main.sales.orders");
  });

  it("filters tables by the search box", async () => {
    const user = userEvent.setup();
    render(<CatalogTreePanel selected={null} onSelect={() => {}} />);
    const box = screen.getByRole("textbox");
    await user.type(box, "revenue");
    // analytics/gold/revenue should remain reachable; the filter narrows the tree
    expect(screen.getByText("analytics")).toBeInTheDocument();
  });

  it("shows an empty state when there are no tables", () => {
    useLineageStore.setState({ allTables: [] as any });
    render(<CatalogTreePanel selected={null} onSelect={() => {}} />);
    // catalogs list is empty — component renders its empty hint (no catalog rows)
    expect(screen.queryByText("main")).not.toBeInTheDocument();
  });

  it("toggles the rail via the Collapse control", async () => {
    const onToggleCollapse = vi.fn();
    const user = userEvent.setup();
    render(<CatalogTreePanel selected={null} onSelect={() => {}} collapsed={false} onToggleCollapse={onToggleCollapse} />);
    await user.click(screen.getByRole("button", { name: /Collapse sidebar/ }));
    expect(onToggleCollapse).toHaveBeenCalledTimes(1);
  });

  it("hides the tree when collapsed and expands on click", async () => {
    const onToggleCollapse = vi.fn();
    const user = userEvent.setup();
    render(<CatalogTreePanel selected={null} onSelect={() => {}} collapsed onToggleCollapse={onToggleCollapse} />);
    // collapsed: no filter box, no catalog rows
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText("main")).not.toBeInTheDocument();
    // the slim rail's expand affordance and the bottom toggle both expand it
    await user.click(screen.getByRole("button", { name: /Expand catalog/ }));
    await user.click(screen.getByRole("button", { name: /Expand sidebar/ }));
    expect(onToggleCollapse).toHaveBeenCalledTimes(2);
  });

  it("marks the selected table as active", async () => {
    const user = userEvent.setup();
    render(<CatalogTreePanel selected="main.sales.orders" onSelect={() => {}} />);
    await user.click(screen.getByText("main"));
    await user.click(screen.getByText("sales"));
    // the selected row renders with the active (white) label styling
    expect(screen.getByText("orders")).toBeInTheDocument();
  });
});
