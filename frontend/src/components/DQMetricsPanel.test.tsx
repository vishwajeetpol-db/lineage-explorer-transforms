import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { DQMetricsPanel } from "./DQMetricsPanel";

function mockFetch(data: any, ok = true) {
  return vi.fn().mockResolvedValue({ ok, json: async () => data, text: async () => (typeof data === "string" ? data : JSON.stringify(data)) });
}

const result = {
  table_fqn: "main.s.t",
  quality_score: 0.95,
  quality_grade: "A",
  rules_evaluated: 3,
  rules_total: 4,
  sample_size: 1000,
  metrics: [
    { rule_id: "r1", column: "email", rule_type: "not_null", pass_rate: 0.995, failing_rows: 5, status: "pass", severity: "high" },
    { rule_id: "r2", column: "age", rule_type: "range", pass_rate: null, status: "error", severity: "low", error: "bad rule" },
  ],
};

describe("DQMetricsPanel", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders header", () => {
    render(<DQMetricsPanel />);
    expect(screen.getByText("Data Quality Metrics")).toBeInTheDocument();
  });

  it("runs checks and renders quality score + metrics", async () => {
    global.fetch = mockFetch(result) as any;
    const user = userEvent.setup();
    render(<DQMetricsPanel tableFqn="main.s.t" />);
    await user.click(screen.getByText("Run Checks"));
    expect(await screen.findByText("95%")).toBeInTheDocument();
    expect(screen.getByText("A")).toBeInTheDocument();
    expect(screen.getByText(/not_null/)).toBeInTheDocument();
    expect(screen.getByText("bad rule")).toBeInTheDocument();
  });

  it("shows empty metrics message", async () => {
    global.fetch = mockFetch({ ...result, metrics: [], quality_grade: null, quality_score: null }) as any;
    const user = userEvent.setup();
    render(<DQMetricsPanel tableFqn="main.s.t" />);
    await user.click(screen.getByText("Run Checks"));
    expect(await screen.findByText(/No DQ rules defined/)).toBeInTheDocument();
    expect(screen.getByText("N/A")).toBeInTheDocument();
  });

  it.each(["B", "D", "F"])("renders grade %s color", async (grade) => {
    global.fetch = mockFetch({ ...result, quality_grade: grade, metrics: [] }) as any;
    const user = userEvent.setup();
    render(<DQMetricsPanel tableFqn="main.s.t" />);
    await user.click(screen.getByText("Run Checks"));
    expect(await screen.findByText(grade)).toBeInTheDocument();
  });

  it("shows error on failure", async () => {
    global.fetch = mockFetch("boom error", false) as any;
    const user = userEvent.setup();
    render(<DQMetricsPanel tableFqn="main.s.t" />);
    await user.click(screen.getByText("Run Checks"));
    expect(await screen.findByText("boom error")).toBeInTheDocument();
  });

  it("does nothing when fqn is empty", async () => {
    const spy = vi.fn();
    global.fetch = spy as any;
    render(<DQMetricsPanel />);
    // Run Checks disabled with empty fqn; button present
    expect(screen.getByText("Run Checks")).toBeDisabled();
    expect(spy).not.toHaveBeenCalled();
  });

  it("runs on Enter key and renders all status/grade variants", async () => {
    const fetchMock = mockFetch({
      table_fqn: "main.s.t", quality_score: 0.5, quality_grade: "C", rules_evaluated: 4, rules_total: 4, sample_size: 50,
      metrics: [
        { rule_id: "1", column: "a", rule_type: "not_null", pass_rate: 1.0, status: "pass", severity: "low" },
        { rule_id: "2", column: "b", rule_type: "unique", pass_rate: 0.95, status: "warn", severity: "med" },
        { rule_id: "3", column: "c", rule_type: "range", pass_rate: 0.5, status: "fail", severity: "high", failing_rows: 25 },
        { rule_id: "4", column: "", rule_type: "custom", pass_rate: null, status: "unknown", severity: "low" },
      ],
    });
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<DQMetricsPanel />);
    const input = screen.getByPlaceholderText("catalog.schema.table");
    await user.type(input, "main.s.t{Enter}");
    expect(await screen.findByText("C")).toBeInTheDocument();
    expect(screen.getAllByText("50%").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("25 failing")).toBeInTheDocument();
  });
});
