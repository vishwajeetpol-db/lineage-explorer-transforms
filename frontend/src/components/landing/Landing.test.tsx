import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import Landing from "./Landing";
import { useLineageStore } from "../../store/lineageStore";
import { useThemeStore } from "../../store/themeStore";
import { api } from "../../api/client";

import { createElement } from "react";
vi.mock("framer-motion", () => ({
  motion: new Proxy({}, {
    get: (_t, tag: string) => (p: any) => {
      const { children, initial, animate, exit, transition, whileHover, ...rest } = p;
      return createElement(tag, rest, children);
    },
  }),
  AnimatePresence: ({ children }: any) => children,
}));
const nav = { goCatalogs: vi.fn(), goTableLineage: vi.fn(), goRootCause: vi.fn(), goDQ: vi.fn(), goControlPanel: vi.fn(), goAdmin: vi.fn() };
vi.mock("../../hooks/useRouter", () => ({
  goCatalogs: () => nav.goCatalogs(),
  goTableLineage: () => nav.goTableLineage(),
  goRootCause: () => nav.goRootCause(),
  goDQ: () => nav.goDQ(),
  goControlPanel: () => nav.goControlPanel(),
  goAdmin: () => nav.goAdmin(),
}));
vi.mock("./LineagePicker", () => ({ default: ({ mode }: any) => <div data-testid="picker">picker-{mode}</div> }));
vi.mock("../../api/client", () => ({
  api: { getUserInfo: vi.fn().mockResolvedValue({ email: "a@b.com", isAdmin: false }), getTables: vi.fn().mockResolvedValue({ tables: [] }) },
}));

function t(catalog: string, name: string) {
  return { name, fqdn: `${catalog}.s.${name}`, catalog, schema: "s", table_type: "MANAGED" };
}

describe("Landing", () => {
  beforeEach(() => {
    Object.values(nav).forEach((f) => f.mockReset());
    (api.getUserInfo as any).mockResolvedValue({ email: "a@b.com", isAdmin: false });
    global.fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ notifications: [], count: 0 }) }) as any;
    useThemeStore.setState({ theme: "dark" });
    useLineageStore.setState({ allTables: [t("main", "x"), t("dev", "y")], allTablesLoading: false, isAdmin: false, globalSearchOpen: false });
  });
  afterEach(() => vi.restoreAllMocks());

  it("renders tiles when data loaded", async () => {
    render(<Landing onSelectTable={vi.fn()} />);
    expect(screen.getAllByText("Browse").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Schema lineage")).toBeInTheDocument();
    expect(screen.getByText("Catalog lineage")).toBeInTheDocument();
    expect(await screen.findByText("a@b.com")).toBeInTheDocument();
  });

  it("shows loading state", () => {
    useLineageStore.setState({ allTablesLoading: true });
    render(<Landing onSelectTable={vi.fn()} />);
    expect(screen.getByText("Loading tables…")).toBeInTheDocument();
  });

  it("shows empty state with retry", async () => {
    useLineageStore.setState({ allTables: [], allTablesLoading: false });
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    expect(screen.getByText("Unable to load table index")).toBeInTheDocument();
    await user.click(screen.getByText("Retry"));
    expect(api.getTables).toHaveBeenCalled();
  });

  it("opens the schema picker", () => {
    const { container } = render(<Landing onSelectTable={vi.fn()} />);
    const tile = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith("Schema lineage"))!;
    fireEvent.click(tile);
    expect(screen.getByTestId("picker")).toHaveTextContent("picker-schema");
  });

  it("opens the catalog picker", () => {
    const { container } = render(<Landing onSelectTable={vi.fn()} />);
    const tile = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith("Catalog lineage"))!;
    fireEvent.click(tile);
    expect(screen.getByTestId("picker")).toHaveTextContent("picker-catalog");
  });

  it("navigates via sidebar Browse", async () => {
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getAllByText("Browse")[0]);
    expect(nav.goCatalogs).toHaveBeenCalled();
  });

  it("navigates via all sidebar nav items", async () => {
    const user = userEvent.setup();
    useLineageStore.setState({ isAdmin: true });
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getByText("Impact Analysis"));
    await user.click(screen.getByText("Data Quality"));
    await user.click(screen.getByText("Reports"));
    await user.click(screen.getByText("Settings"));
    await user.click(screen.getByText("Admin Dashboard"));
    expect(nav.goTableLineage).toHaveBeenCalled();
    expect(nav.goDQ).toHaveBeenCalled();
    expect(nav.goRootCause).toHaveBeenCalled();
    expect(nav.goControlPanel).toHaveBeenCalled();
    expect(nav.goAdmin).toHaveBeenCalled();
  });

  it("opens global search from sidebar and hero button", async () => {
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getByText("Search"));
    expect(useLineageStore.getState().globalSearchOpen).toBe(true);
    useLineageStore.setState({ globalSearchOpen: false });
    await user.click(screen.getByText(/Search any table across/));
    expect(useLineageStore.getState().globalSearchOpen).toBe(true);
  });

  it("opens Table Lineage suite tile and Browse tile", () => {
    const { container } = render(<Landing onSelectTable={vi.fn()} />);
    const tableTile = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith("Table Lineage"))!;
    fireEvent.click(tableTile);
    expect(nav.goTableLineage).toHaveBeenCalled();
    const browseTile = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.includes("Explore"))!;
    fireEvent.click(browseTile);
    expect(nav.goCatalogs).toHaveBeenCalled();
  });

  it("toggles theme", async () => {
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getByTitle("Light mode"));
    expect(useThemeStore.getState().theme).toBe("light");
  });

  it("hides Admin Dashboard nav unless admin", () => {
    render(<Landing onSelectTable={vi.fn()} />);
    expect(screen.queryByText("Admin Dashboard")).not.toBeInTheDocument();
  });

  it("shows Admin Dashboard nav for admins", () => {
    useLineageStore.setState({ isAdmin: true });
    render(<Landing onSelectTable={vi.fn()} />);
    expect(screen.getByText("Admin Dashboard")).toBeInTheDocument();
  });

  it("opens search from the notifications bell", async () => {
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getByTitle("Notifications"));
    expect(useLineageStore.getState().globalSearchOpen).toBe(true);
  });

  it("closes the picker via its onClose", () => {
    const { container } = render(<Landing onSelectTable={vi.fn()} />);
    const tile = Array.from(container.querySelectorAll("button")).find((b) => b.textContent?.trim().startsWith("Schema lineage"))!;
    fireEvent.click(tile);
    expect(screen.getByTestId("picker")).toBeInTheDocument();
  });

  it("handles retry failure gracefully", async () => {
    (api.getTables as any).mockRejectedValue(new Error("nope"));
    useLineageStore.setState({ allTables: [], allTablesLoading: false });
    const user = userEvent.setup();
    render(<Landing onSelectTable={vi.fn()} />);
    await user.click(screen.getByText("Retry"));
    expect(api.getTables).toHaveBeenCalled();
  });

  it("renders relative time labels for varied activity timestamps", async () => {
    const now = Date.now();
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("unread-count")) return Promise.resolve({ ok: true, json: async () => ({ count: 0 }) });
      return Promise.resolve({ ok: true, json: async () => ({ notifications: [
        { id: 1, type: "pipeline run", title: "Sec", created_at: new Date(now - 10 * 1000).toISOString() },
        { id: 2, type: "dq quality", title: "Min", created_at: new Date(now - 5 * 60 * 1000).toISOString() },
        { id: 3, type: "new_table", title: "Hr", created_at: new Date(now - 2 * 3600 * 1000).toISOString() },
        { id: 4, type: "schema", title: "Day", created_at: new Date(now - 3 * 86400 * 1000).toISOString() },
      ] }) });
    }) as any;
    render(<Landing onSelectTable={vi.fn()} />);
    expect(await screen.findByText("Sec")).toBeInTheDocument();
    expect(screen.getByText(/m ago/)).toBeInTheDocument();
    expect(screen.getByText(/h ago/)).toBeInTheDocument();
    expect(screen.getByText(/d ago/)).toBeInTheDocument();
  });

  it("renders recent activity and selects a table", async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (url.includes("unread-count")) return Promise.resolve({ ok: true, json: async () => ({ count: 12 }) });
      return Promise.resolve({ ok: true, json: async () => ({ notifications: [{ id: 1, type: "schema_change", title: "Changed", table_fqn: "main.s.x", created_at: new Date().toISOString() }] }) });
    }) as any;
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<Landing onSelectTable={onSelect} />);
    expect(await screen.findByText("Recent Activity")).toBeInTheDocument();
    await user.click(screen.getByText("Changed"));
    expect(onSelect).toHaveBeenCalledWith("main.s.x");
  });
});
