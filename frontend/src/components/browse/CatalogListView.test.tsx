import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import CatalogListView from "./CatalogListView";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const goSchemas = vi.fn();
const goCatalogLineage = vi.fn();
vi.mock("../../hooks/useRouter", () => ({
  goSchemas: (c: string) => goSchemas(c),
  goCatalogLineage: (c: string) => goCatalogLineage(c),
  goLanding: vi.fn(), goCatalogs: vi.fn(), goControlPanel: vi.fn(), goDQ: vi.fn(),
  goGlossary: vi.fn(), goNotifications: vi.fn(), goExport: vi.fn(), goRootCause: vi.fn(),
  goBiConsumers: vi.fn(), goStreaming: vi.fn(),
}));

function t(catalog: string, schema: string, name: string): TableSearchItem {
  return { name, fqdn: `${catalog}.${schema}.${name}`, catalog, schema, table_type: "MANAGED" };
}

describe("CatalogListView", () => {
  beforeEach(() => {
    useLineageStore.setState({
      allTables: [t("main", "sales", "orders"), t("main", "hr", "emp"), t("dev", "s1", "x")],
      isAdmin: false,
    });
  });

  it("aggregates catalog stats", () => {
    render(<CatalogListView />);
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("2 schemas")).toBeInTheDocument();
  });

  it("filters catalogs and shows empty state", () => {
    render(<CatalogListView />);
    fireEvent.change(screen.getByPlaceholderText("Filter catalogs..."), { target: { value: "dev" } });
    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.queryByText("main")).not.toBeInTheDocument();
    fireEvent.change(screen.getByPlaceholderText("Filter catalogs..."), { target: { value: "zzz" } });
    expect(screen.getByText(/No catalogs matching/)).toBeInTheDocument();
  });

  it("navigates to schemas and catalog lineage", async () => {
    const user = userEvent.setup();
    render(<CatalogListView />);
    await user.click(screen.getByText("dev"));
    expect(goSchemas).toHaveBeenCalledWith("dev");
    const lineageBtns = screen.getAllByText("View full lineage");
    await user.click(lineageBtns[0]);
    expect(goCatalogLineage).toHaveBeenCalled();
  });
});
