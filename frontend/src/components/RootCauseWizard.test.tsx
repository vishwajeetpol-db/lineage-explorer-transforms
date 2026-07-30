import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { RootCauseWizard } from "./RootCauseWizard";

function mockFetch(data: any, ok = true) {
  return vi.fn().mockResolvedValue({ ok, json: async () => data, text: async () => (typeof data === "string" ? data : JSON.stringify(data)) });
}

const withCandidates = {
  summary: "found stuff",
  upstream_path: [],
  timeline: [],
  candidates: [
    { candidate_table: "main.s.up", candidate_column: "col", hop_distance: 2, score: 0.9, evidence: ["job failed", "dq drop"] },
  ],
};

describe("RootCauseWizard", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the input form", () => {
    render(<RootCauseWizard />);
    expect(screen.getByText("Root Cause Analysis")).toBeInTheDocument();
    expect(screen.getByText("Analyze Root Cause")).toBeInTheDocument();
  });

  it("disables analyze until required fields present", () => {
    render(<RootCauseWizard />);
    expect(screen.getByText("Analyze Root Cause")).toBeDisabled();
  });

  it("runs analysis and shows candidates", async () => {
    global.fetch = mockFetch(withCandidates) as any;
    const user = userEvent.setup();
    render(<RootCauseWizard catalog="c" schema="s" table="t" column="col" />);
    await user.click(screen.getByText("Analyze Root Cause"));
    expect(await screen.findByText(/1 candidate found/)).toBeInTheDocument();
    expect(screen.getByText("job failed")).toBeInTheDocument();
  });

  it("shows the no-root-cause result", async () => {
    global.fetch = mockFetch({ ...withCandidates, candidates: [] }) as any;
    const user = userEvent.setup();
    render(<RootCauseWizard catalog="c" schema="s" table="t" column="col" />);
    await user.click(screen.getByText("Analyze Root Cause"));
    expect(await screen.findByText("No likely root cause found")).toBeInTheDocument();
  });

  it("returns to input on error", async () => {
    global.fetch = mockFetch("analysis broke", false) as any;
    const user = userEvent.setup();
    render(<RootCauseWizard catalog="c" schema="s" table="t" column="col" />);
    await user.click(screen.getByText("Analyze Root Cause"));
    expect(await screen.findByText("analysis broke")).toBeInTheDocument();
  });

  it("can run another analysis from results", async () => {
    global.fetch = mockFetch(withCandidates) as any;
    const user = userEvent.setup();
    render(<RootCauseWizard catalog="c" schema="s" table="t" column="col" />);
    await user.click(screen.getByText("Analyze Root Cause"));
    await screen.findByText(/1 candidate found/);
    await user.click(screen.getByText(/Run another analysis/));
    expect(screen.getByText("Analyze Root Cause")).toBeInTheDocument();
  });
});
