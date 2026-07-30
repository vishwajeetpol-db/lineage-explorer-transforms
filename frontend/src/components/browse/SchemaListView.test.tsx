import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import SchemaListView from "./SchemaListView";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const goTables = vi.fn();
const goSchemaLineage = vi.fn();
const goCatalogLineage = vi.fn();
vi.mock("../../hooks/useRouter", () => ({
  goTables: (c: string, s: string) => goTables(c, s),
  goSchemaLineage: (c: string, s: string) => goSchemaLineage(c, s),
  goCatalogLineage: (c: string) => goCatalogLineage(c),
  goLanding: vi.fn(), goCatalogs: vi.fn(), goControlPanel: vi.fn(), goDQ: vi.fn(),
  goGlossary: vi.fn(), goNotifications: vi.fn(), goExport: vi.fn(), goRootCause: vi.fn(),
  goBiConsumers: vi.fn(), goStreaming: vi.fn(),
}));

function t(catalog: string, schema: string, name: string): TableSearchItem {
  return { name, fqdn: `${catalog}.${schema}.${name}`, catalog, schema, table_type: "MANAGED" };
}

describe("SchemaListView", () => {
  beforeEach(() => {
    useLineageStore.setState({
      allTables: [t("main", "bronze", "a"), t("main", "gold", "b"), t("main", "misc", "c")],
      isAdmin: false,
    });
  });

  it("lists schemas with medallion badge ordering", () => {
    render(<SchemaListView catalog="main" />);
    // bronze/gold appear twice (name + badge); misc once
    expect(screen.getAllByText("bronze").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("gold").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("misc")).toBeInTheDocument();
  });

  it("shows empty state when no schemas in catalog", () => {
    render(<SchemaListView catalog="nope" />);
    expect(screen.getByText(/No accessible schemas/)).toBeInTheDocument();
  });

  it("filters and shows no-match state", () => {
    render(<SchemaListView catalog="main" />);
    fireEvent.change(screen.getByPlaceholderText("Filter schemas..."), { target: { value: "zzz" } });
    expect(screen.getByText(/No schemas matching/)).toBeInTheDocument();
  });

  it("navigates to tables, schema lineage and catalog lineage", async () => {
    const user = userEvent.setup();
    render(<SchemaListView catalog="main" />);
    await user.click(screen.getByText("misc"));
    expect(goTables).toHaveBeenCalledWith("main", "misc");
    await user.click(screen.getAllByText("Lineage")[0]);
    expect(goSchemaLineage).toHaveBeenCalled();
    await user.click(screen.getByText("View catalog lineage"));
    expect(goCatalogLineage).toHaveBeenCalledWith("main");
  });
});
