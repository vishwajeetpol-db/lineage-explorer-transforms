import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import HeaderMenu from "./HeaderMenu";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const nav = {
  goLanding: vi.fn(), goCatalogs: vi.fn(), goControlPanel: vi.fn(), goDQ: vi.fn(),
  goGlossary: vi.fn(), goNotifications: vi.fn(), goExport: vi.fn(), goRootCause: vi.fn(),
  goBiConsumers: vi.fn(), goStreaming: vi.fn(),
};
vi.mock("../../hooks/useRouter", () => ({
  goLanding: () => nav.goLanding(),
  goCatalogs: () => nav.goCatalogs(),
  goControlPanel: () => nav.goControlPanel(),
  goDQ: () => nav.goDQ(),
  goGlossary: () => nav.goGlossary(),
  goNotifications: () => nav.goNotifications(),
  goExport: () => nav.goExport(),
  goRootCause: () => nav.goRootCause(),
  goBiConsumers: () => nav.goBiConsumers(),
  goStreaming: () => nav.goStreaming(),
}));

describe("HeaderMenu", () => {
  beforeEach(() => {
    useLineageStore.setState({ isAdmin: false });
  });

  it("toggles the menu open on click", async () => {
    const user = userEvent.setup();
    render(<HeaderMenu />);
    expect(screen.queryByText("Browse catalogs")).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Open menu"));
    expect(screen.getByText("Browse catalogs")).toBeInTheDocument();
  });

  it("navigates via every menu item", async () => {
    const user = userEvent.setup();
    render(<HeaderMenu />);
    const items: [string, keyof typeof nav][] = [
      ["Home", "goLanding"],
      ["Browse catalogs", "goCatalogs"],
      ["Control Panel", "goControlPanel"],
      ["Data Quality", "goDQ"],
      ["Business Glossary", "goGlossary"],
      ["Notifications", "goNotifications"],
      ["OpenLineage Export", "goExport"],
      ["Root Cause Analysis", "goRootCause"],
      ["BI Consumers", "goBiConsumers"],
      ["Streaming Topology", "goStreaming"],
    ];
    for (const [label, fn] of items) {
      await user.click(screen.getByLabelText("Open menu"));
      await user.click(screen.getByText(label));
      expect(nav[fn]).toHaveBeenCalled();
    }
  });

  it("closes the menu via the overlay", async () => {
    const user = userEvent.setup();
    const { container } = render(<HeaderMenu />);
    await user.click(screen.getByLabelText("Open menu"));
    expect(screen.getByText("Home")).toBeInTheDocument();
    // overlay is the first fixed-inset div sibling
    const overlay = container.querySelector(".fixed.inset-0") as HTMLElement;
    await user.click(overlay);
    expect(screen.queryByText("Home")).not.toBeInTheDocument();
  });

  it("hides admin link unless admin", async () => {
    const user = userEvent.setup();
    render(<HeaderMenu />);
    await user.click(screen.getByLabelText("Open menu"));
    expect(screen.queryByText("Admin Dashboard")).not.toBeInTheDocument();
  });

  it("shows admin link when isAdmin", async () => {
    useLineageStore.setState({ isAdmin: true });
    const user = userEvent.setup();
    render(<HeaderMenu variant="floating" />);
    await user.click(screen.getByLabelText("Open menu"));
    expect(screen.getByText("Admin Dashboard")).toBeInTheDocument();
  });
});
