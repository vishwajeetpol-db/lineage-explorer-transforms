import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ErrorBoundary from "./ErrorBoundary";

function Boom({ msg }: { msg?: string }): JSX.Element {
  throw new Error(msg ?? "kaboom");
}

describe("ErrorBoundary", () => {
  let errSpy: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    errSpy = vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    errSpy.mockRestore();
  });

  it("renders children when no error", () => {
    render(
      <ErrorBoundary>
        <div>safe child</div>
      </ErrorBoundary>,
    );
    expect(screen.getByText("safe child")).toBeInTheDocument();
  });

  it("renders fallback with the error message when a child throws", () => {
    render(
      <ErrorBoundary>
        <Boom msg="specific failure" />
      </ErrorBoundary>,
    );
    expect(screen.getByText("Something went wrong")).toBeInTheDocument();
    expect(screen.getByText("specific failure")).toBeInTheDocument();
    expect(screen.getByText("Reload App")).toBeInTheDocument();
  });

  it("shows a default message when the error has none", () => {
    render(
      <ErrorBoundary>
        <Boom msg="" />
      </ErrorBoundary>,
    );
    expect(screen.getByText("An unexpected error occurred")).toBeInTheDocument();
  });

  it("attempts to recover and reload on button click", async () => {
    const reload = vi.fn();
    Object.defineProperty(window, "location", {
      value: { ...window.location, reload },
      writable: true,
      configurable: true,
    });
    const user = userEvent.setup();
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>,
    );
    await user.click(screen.getByText("Reload App"));
    expect(reload).toHaveBeenCalled();
  });
});
