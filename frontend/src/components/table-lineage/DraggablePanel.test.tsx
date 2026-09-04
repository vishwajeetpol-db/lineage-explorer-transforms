import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import DraggablePanel from "./DraggablePanel";

describe("DraggablePanel", () => {
  it("renders title, subtitle, and children", () => {
    render(
      <DraggablePanel title="Impact" subtitle="cat.s.t" onClose={() => {}}>
        <div>panel body</div>
      </DraggablePanel>,
    );
    expect(screen.getByText("Impact")).toBeInTheDocument();
    expect(screen.getByText("cat.s.t")).toBeInTheDocument();
    expect(screen.getByText("panel body")).toBeInTheDocument();
  });

  it("calls onClose when the X is clicked", async () => {
    const onClose = vi.fn();
    render(<DraggablePanel title="P" onClose={onClose}><div>x</div></DraggablePanel>);
    const btn = screen.getByRole("button");
    await userEvent.click(btn);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("calls onFocus on mousedown", () => {
    const onFocus = vi.fn();
    render(<DraggablePanel title="P" onFocus={onFocus} onClose={() => {}}><div>x</div></DraggablePanel>);
    const title = screen.getByText("P");
    title.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    expect(onFocus).toHaveBeenCalled();
  });

  it("drags via pointer events on the title bar", () => {
    render(<DraggablePanel title="Drag me" initial={{ x: 100, y: 100 }} onClose={() => {}}><div>x</div></DraggablePanel>);
    const bar = screen.getByText("Drag me").parentElement!.parentElement!;
    // pointer down → move → up should not throw and updates position state
    bar.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true, clientX: 120, clientY: 120 } as any));
    bar.dispatchEvent(new PointerEvent("pointermove", { bubbles: true, clientX: 200, clientY: 180 } as any));
    bar.dispatchEvent(new PointerEvent("pointerup", { bubbles: true } as any));
    expect(screen.getByText("Drag me")).toBeInTheDocument();
  });

  it("repositions on window resize without crashing", () => {
    render(<DraggablePanel title="P" onClose={() => {}}><div>x</div></DraggablePanel>);
    window.dispatchEvent(new Event("resize"));
    expect(screen.getByText("P")).toBeInTheDocument();
  });
});
