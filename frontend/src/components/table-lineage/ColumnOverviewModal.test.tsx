import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ColumnOverviewModal from "./ColumnOverviewModal";
import { api } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getColumnTransformationOverview: vi.fn() },
}));

const TABLE = "cat.schema.orders_curated";

const RESULT = {
  table_full_name: TABLE,
  source: "plan_capture",
  source_label: "Captured plan v1",
  version: 1,
  summary: "This table curates orders by converting amounts to USD.",
  columns: [
    { target_column: "amount_usd", source_columns: ["amount", "fx_rate"], expression: "amount * fx_rate", category: "ARITHMETIC", explanation: "Multiplies amount by fx_rate to get USD." },
    { target_column: "status", source_columns: ["raw_status"], expression: "UPPER(raw_status)", category: "STRING", explanation: "Uppercases the raw status." },
  ],
};

describe("ColumnOverviewModal", () => {
  beforeEach(() => {
    (api.getColumnTransformationOverview as any).mockResolvedValue(RESULT);
  });

  it("shows loading then the summary and first column's graphic + explanation", async () => {
    render(<ColumnOverviewModal table={TABLE} onClose={() => {}} />);
    expect(screen.getByText(/Generating overview/i)).toBeInTheDocument();
    // summary renders
    expect(await screen.findByText(/converting amounts to USD/i)).toBeInTheDocument();
    // first column auto-selected → its graphic (expression) + explanation show
    expect(screen.getByText("amount * fx_rate")).toBeInTheDocument();
    expect(screen.getByText(/Multiplies amount by fx_rate/i)).toBeInTheDocument();
    // source + target column appear in the graphic
    expect(screen.getByText("fx_rate")).toBeInTheDocument();
  });

  it("switches the graphic when another column is selected", async () => {
    const user = userEvent.setup();
    render(<ColumnOverviewModal table={TABLE} onClose={() => {}} />);
    await screen.findByText(/converting amounts to USD/i);
    // select the second column (list button)
    await user.click(screen.getByRole("button", { name: /status/i }));
    expect(await screen.findByText("UPPER(raw_status)")).toBeInTheDocument();
    expect(screen.getByText(/Uppercases the raw status/i)).toBeInTheDocument();
  });

  it("regenerates with refresh=true", async () => {
    const user = userEvent.setup();
    render(<ColumnOverviewModal table={TABLE} onClose={() => {}} />);
    await screen.findByText(/converting amounts to USD/i);
    await user.click(screen.getByTitle(/Regenerate/i));
    await waitFor(() =>
      expect((api.getColumnTransformationOverview as any).mock.calls.some((c: any[]) => c[0]?.refresh === true)).toBe(true),
    );
  });

  it("calls onClose from the close button", async () => {
    const onClose = vi.fn();
    const user = userEvent.setup();
    render(<ColumnOverviewModal table={TABLE} onClose={onClose} />);
    await screen.findByText(/converting amounts to USD/i);
    await user.click(screen.getByLabelText(/close overview/i));
    expect(onClose).toHaveBeenCalled();
  });

  it("shows the LLM-failed banner and handles a constant column with no source columns", async () => {
    (api.getColumnTransformationOverview as any).mockResolvedValue({
      ...RESULT,
      error: "llm timeout",
      summary: "",
      columns: [
        { target_column: "created_at", source_columns: [], expression: "current_timestamp()", category: "CONSTANT", explanation: "" },
      ],
    });
    render(<ColumnOverviewModal table={TABLE} onClose={() => {}} />);
    expect(await screen.findByText(/couldn.t be generated/i)).toBeInTheDocument();
    // constant column with no sources renders the placeholder + the fallback explanation
    expect(screen.getByText(/constant \/ generated/i)).toBeInTheDocument();
    expect(screen.getByText(/No explanation available/i)).toBeInTheDocument();
  });

  it("surfaces an error with a retry", async () => {
    (api.getColumnTransformationOverview as any).mockRejectedValue(new Error("llm boom"));
    render(<ColumnOverviewModal table={TABLE} onClose={() => {}} />);
    expect(await screen.findByText(/llm boom/i)).toBeInTheDocument();
    expect(screen.getByText("Retry")).toBeInTheDocument();
  });
});
