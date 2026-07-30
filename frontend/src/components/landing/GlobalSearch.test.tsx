import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import GlobalSearch from "./GlobalSearch";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

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
let mockRecents: string[] = [];
vi.mock("../../hooks/useRecents", () => ({
  useRecents: () => ({ recents: mockRecents, addRecent: vi.fn(), clearRecents: vi.fn() }),
}));

function t(name: string, table_type = "MANAGED"): TableSearchItem {
  return { name, fqdn: `main.sales.${name}`, catalog: "main", schema: "sales", table_type };
}
const tables = [t("orders"), t("order_items"), t("customers", "VIEW"), t("mv1", "MATERIALIZED_VIEW")];

describe("GlobalSearch", () => {
  beforeEach(() => {
    mockRecents = [];
    useLineageStore.setState({ globalSearchOpen: true, allTables: tables });
  });

  it("renders nothing when closed", () => {
    useLineageStore.setState({ globalSearchOpen: false });
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    expect(screen.queryByPlaceholderText(/Search tables across/)).not.toBeInTheDocument();
  });

  it("shows the start-typing prompt when no query and no recents", () => {
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    expect(screen.getByText(/Start typing to search across/)).toBeInTheDocument();
  });

  it("shows recents when present", () => {
    mockRecents = ["main.sales.orders"];
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    expect(screen.getByText("Recent")).toBeInTheDocument();
  });

  it("matches tables by query", () => {
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search tables across/), { target: { value: "order" } });
    expect(screen.getByText("Matches")).toBeInTheDocument();
    expect(screen.getAllByText(/order/i).length).toBeGreaterThan(0);
  });

  it("shows no-match state", () => {
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search tables across/), { target: { value: "zzzz" } });
    expect(screen.getByText(/No tables matching/)).toBeInTheDocument();
  });

  it("selects a match on click", async () => {
    const onSelect = vi.fn();
    const user = userEvent.setup();
    render(<GlobalSearch onSelectTable={onSelect} />);
    fireEvent.change(screen.getByPlaceholderText(/Search tables across/), { target: { value: "customers" } });
    await user.click(screen.getByText("customers").closest("button")!);
    expect(onSelect).toHaveBeenCalledWith("main.sales.customers");
    expect(useLineageStore.getState().globalSearchOpen).toBe(false);
  });

  it("handles arrow + enter keys without crashing", () => {
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    const q = () => screen.getByPlaceholderText(/Search tables across/);
    fireEvent.change(q(), { target: { value: "order" } });
    fireEvent.keyDown(q(), { key: "ArrowDown" });
    fireEvent.keyDown(q(), { key: "ArrowDown" });
    fireEvent.keyDown(q(), { key: "ArrowUp" });
    // handler branches exercised; dialog remains rendered
    expect(screen.getByText("Matches")).toBeInTheDocument();
  });

  it("closes on escape key via window handler", () => {
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(useLineageStore.getState().globalSearchOpen).toBe(false);
  });

  it("toggles open with cmd+k when closed", () => {
    useLineageStore.setState({ globalSearchOpen: false });
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    expect(useLineageStore.getState().globalSearchOpen).toBe(true);
  });

  it("closes when the overlay is clicked", () => {
    const { container } = render(<GlobalSearch onSelectTable={vi.fn()} />);
    const overlay = container.querySelector(".fixed.inset-0") as HTMLElement;
    fireEvent.click(overlay);
    expect(useLineageStore.getState().globalSearchOpen).toBe(false);
  });

  it("selects the active match via Enter key", () => {
    const onSelect = vi.fn();
    render(<GlobalSearch onSelectTable={onSelect} />);
    const q = () => screen.getByPlaceholderText(/Search tables across/);
    fireEvent.change(q(), { target: { value: "customers" } });
    fireEvent.keyDown(q(), { key: "Enter" });
    expect(onSelect).toHaveBeenCalledWith("main.sales.customers");
  });

  it("navigates down and up with arrow keys then selects", () => {
    const onSelect = vi.fn();
    render(<GlobalSearch onSelectTable={onSelect} />);
    const q = () => screen.getByPlaceholderText(/Search tables across/);
    fireEvent.change(q(), { target: { value: "order" } });
    fireEvent.keyDown(q(), { key: "ArrowDown" });
    fireEvent.keyDown(q(), { key: "ArrowUp" });
    fireEvent.keyDown(q(), { key: "Enter" });
    expect(onSelect).toHaveBeenCalled();
  });

  it("shows the showing-N-of-M counter when matches exceed cap", () => {
    const many = Array.from({ length: 20 }, (_, i) => t(`order_${i}`));
    useLineageStore.setState({ globalSearchOpen: true, allTables: many });
    render(<GlobalSearch onSelectTable={vi.fn()} />);
    fireEvent.change(screen.getByPlaceholderText(/Search tables across/), { target: { value: "order" } });
    expect(screen.getByText(/showing 12 of 20/)).toBeInTheDocument();
  });
});
