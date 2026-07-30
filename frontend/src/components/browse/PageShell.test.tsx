import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import PageShell from "./PageShell";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div onClick={p.onClick}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));
const goLanding = vi.fn();
vi.mock("../../hooks/useRouter", () => ({
  goLanding: () => goLanding(),
  goCatalogs: vi.fn(),
  goControlPanel: vi.fn(),
  goDQ: vi.fn(),
  goGlossary: vi.fn(),
  goNotifications: vi.fn(),
  goExport: vi.fn(),
  goRootCause: vi.fn(),
  goBiConsumers: vi.fn(),
  goStreaming: vi.fn(),
}));

describe("PageShell", () => {
  beforeEach(() => {
    useLineageStore.setState({ isAdmin: false, globalSearchOpen: false });
  });

  it("renders children and brand", () => {
    render(<PageShell><div>content here</div></PageShell>);
    expect(screen.getByText("content here")).toBeInTheDocument();
    expect(screen.getByText("BrickTrace")).toBeInTheDocument();
  });

  it("opens global search on button click", async () => {
    const user = userEvent.setup();
    render(<PageShell><div>x</div></PageShell>);
    await user.click(screen.getByText("Search any table..."));
    expect(useLineageStore.getState().globalSearchOpen).toBe(true);
  });
});
