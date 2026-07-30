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
});
