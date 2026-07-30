import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import LineagePicker from "./LineagePicker";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const goSchemaLineage = vi.fn();
const goCatalogLineage = vi.fn();
vi.mock("../../hooks/useRouter", () => ({
  goSchemaLineage: (c: string, s: string) => goSchemaLineage(c, s),
  goCatalogLineage: (c: string) => goCatalogLineage(c),
}));

function t(catalog: string, schema: string, name: string): TableSearchItem {
  return { name, fqdn: `${catalog}.${schema}.${name}`, catalog, schema, table_type: "MANAGED" };
}

describe("LineagePicker", () => {
  beforeEach(() => {
    goSchemaLineage.mockReset();
    goCatalogLineage.mockReset();
    useLineageStore.setState({ allTables: [t("main", "sales", "a"), t("main", "hr", "b"), t("dev", "x", "c")] });
  });

  it("lists catalogs in catalog mode and navigates", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LineagePicker mode="catalog" onClose={onClose} />);
    expect(screen.getByText("Catalog lineage")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    await user.click(screen.getByText("main"));
    expect(goCatalogLineage).toHaveBeenCalledWith("main");
    expect(onClose).toHaveBeenCalled();
  });

  it("lists schema pairs in schema mode and navigates", async () => {
    const user = userEvent.setup();
    render(<LineagePicker mode="schema" onClose={vi.fn()} />);
    expect(screen.getByText("Schema lineage")).toBeInTheDocument();
    expect(screen.getByText("sales")).toBeInTheDocument();
    await user.click(screen.getByText("sales"));
    expect(goSchemaLineage).toHaveBeenCalledWith("main", "sales");
  });

  it("filters catalogs and shows empty state", () => {
    render(<LineagePicker mode="catalog" onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("Filter catalogs..."), { target: { value: "zzz" } });
    expect(screen.getByText("No catalogs found")).toBeInTheDocument();
  });

  it("filters schemas and shows empty state", () => {
    render(<LineagePicker mode="schema" onClose={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText("Filter schemas..."), { target: { value: "zzz" } });
    expect(screen.getByText("No schemas found")).toBeInTheDocument();
  });

  it("closes on escape and on close button", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<LineagePicker mode="catalog" onClose={onClose} />);
    await user.click(screen.getByLabelText("Close"));
    expect(onClose).toHaveBeenCalled();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });
});
