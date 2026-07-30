import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import BuildProgress from "./BuildProgress";
import type { BuildJobStatus } from "../../api/transform";

vi.mock("framer-motion", () => ({
  motion: new Proxy({}, { get: () => (p: any) => <div style={p.style}>{p.children}</div> }),
  AnimatePresence: ({ children }: any) => children,
}));

function status(overrides: Partial<BuildJobStatus> = {}): BuildJobStatus {
  return {
    run_id: "r1", state: "RUNNING", result_state: null, state_message: "", progress_pct: 40,
    is_complete: false, is_success: false, current_step: 1, current_step_name: "Parsing",
    total_steps: 3, steps: ["Fetch", "Parse", "Build"], run_page_url: "https://x/run", ...overrides,
  };
}

describe("BuildProgress", () => {
  it("shows submitting state when status is null", () => {
    render(<BuildProgress status={null} />);
    expect(screen.getByText(/Submitting build job/)).toBeInTheDocument();
  });

  it("shows in-progress header, steps and job link", () => {
    render(<BuildProgress status={status()} />);
    expect(screen.getByText("Building Transformation Lineage")).toBeInTheDocument();
    expect(screen.getByText("Parsing")).toBeInTheDocument();
    expect(screen.getByText("Fetch")).toBeInTheDocument();
    expect(screen.getByText("View job run")).toBeInTheDocument();
  });

  it("shows complete + success state", () => {
    render(<BuildProgress status={status({ is_complete: true, is_success: true, progress_pct: 100, current_step: 3 })} />);
    expect(screen.getByText("Build Complete")).toBeInTheDocument();
    expect(screen.getByText("Loading results…")).toBeInTheDocument();
  });

  it("shows failed state with message", () => {
    render(<BuildProgress status={status({ is_complete: true, is_success: false, state_message: "kaput", current_step: 2 })} />);
    expect(screen.getByText("Build Failed")).toBeInTheDocument();
    expect(screen.getByText("kaput")).toBeInTheDocument();
  });

  it("omits the job link when there is no run url", () => {
    render(<BuildProgress status={status({ run_page_url: "" })} />);
    expect(screen.queryByText("View job run")).not.toBeInTheDocument();
  });
});
