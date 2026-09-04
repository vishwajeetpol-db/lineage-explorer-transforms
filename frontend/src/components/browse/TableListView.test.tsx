import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TableListView from "./TableListView";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <button onClick={p.onClick}>{p.children}</button> }),
  AnimatePresence: ({ children }: any) => children,
}));
vi.mock("../../hooks/useRouter", () => ({
  goLanding: vi.fn(), goCatalogs: vi.fn(), goControlPanel: vi.fn(), goDQ: vi.fn(),
  goGlossary: vi.fn(), goNotifications: vi.fn(), goExport: vi.fn(), goRootCause: vi.fn(),
  goBiConsumers: vi.fn(), goStreaming: vi.fn(),
}));

function t(name: string, table_type = "MANAGED"): TableSearchItem {
  return { name, fqdn: `main.sales.${name}`, catalog: "main", schema: "sales", table_type };
}

describe("TableListView", () => {
  beforeEach(() => {
    useLineageStore.setState({
      allTables: [t("orders"), t("customers", "VIEW"), t("mv1", "MATERIALIZED_VIEW")],
      isAdmin: false,
    });
  });

  it("lists tables in the schema with type badges", () => {
    render(<TableListView catalog="main" schema="sales" onSelectTable={vi.fn()} />);
    expect(screen.getByText("orders")).toBeInTheDocument();
    expect(screen.getByText("customers")).toBeInTheDocument();
    expect(screen.getByText("MV")).toBeInTheDocument();
  });

  it("shows empty state when no tables", () => {
    render(<TableListView catalog="main" schema="empty" onSelectTable={vi.fn()} />);
    expect(screen.getByText("No tables in this schema.")).toBeInTheDocument();
  });

  it("filters and shows no-match state", () => {
    render(<TableListView catalog="main" schema="sales" onSelectTable={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("Filter tables..."), { target: { value: "zzz" } });
    expect(screen.getByText(/No tables matching/)).toBeInTheDocument();
  });

  it("selects a table on click", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<TableListView catalog="main" schema="sales" onSelectTable={onSelect} />);
    await user.click(screen.getByText("orders"));
    expect(onSelect).toHaveBeenCalledWith("main.sales.orders");
  });
});
