import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, act } from "@testing-library/react";
import Skeleton from "./Skeleton";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

describe("Skeleton", () => {
  beforeEach(() => {
    useLineageStore.setState({ liveMode: false });
  });

  it("renders the cached first stage by default", () => {
    const { getByText } = render(<Skeleton />);
    expect(getByText("Checking cache...")).toBeInTheDocument();
  });

  it("advances stages over time in cached mode", () => {
    vi.useFakeTimers();
    const { getByText } = render(<Skeleton />);
    act(() => {
      vi.advanceTimersByTime(2500);
    });
    expect(getByText("Rendering graph...")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders live-mode stages when liveMode is on", () => {
    useLineageStore.setState({ liveMode: true });
    const { getByText } = render(<Skeleton />);
    expect(getByText("Connecting to warehouse...")).toBeInTheDocument();
  });
});
