import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ColumnTransformationPanel from "./ColumnTransformationPanel";
import { api } from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";

vi.mock("../../api/client", () => ({
  api: {
    getAnalyzeModels: vi.fn(),
    resolveColumnTransformations: vi.fn(),
    listColumnTransformationVersions: vi.fn(),
    compareTransformationVersions: vi.fn(),
    getTransformationVersion: vi.fn(),
    compareProducers: vi.fn(),
  },
}));

const TABLE = "cat.schema.orders_curated";

function seedProducers() {
  useLineageStore.setState({
    nodes: [
      { node_type: "entity", id: "entity:JOB:a", entity_type: "JOB", entity_id: "a", display_name: "prod_a" },
      { node_type: "entity", id: "entity:JOB:b", entity_type: "JOB", entity_id: "b", display_name: "prod_b" },
    ] as any,
    edges: [
      { source: "entity:JOB:a", target: TABLE },
      { source: "entity:JOB:b", target: TABLE },
    ] as any,
  });
}

beforeEach(() => {
  useLineageStore.setState({ nodes: [], edges: [] } as any);
  (api.getAnalyzeModels as any).mockResolvedValue({ models: ["m1"], default: "m1" });
  (api.listColumnTransformationVersions as any).mockResolvedValue({ versions: [] });
  (api.resolveColumnTransformations as any).mockResolvedValue({
    source: "plan_capture", source_label: "Captured plan v1", columns: [
      { target_column: "amount_usd", source_columns: ["amount"], expression: "amount*1.1", category: "ARITHMETIC" },
    ], version: 1,
  });
});

describe("ColumnTransformationPanel", () => {
  it("shows NoTable when no table", () => {
    render(<ColumnTransformationPanel table={null} />);
    expect(screen.getByText(/select a table/i)).toBeInTheDocument();
  });

  it("resolves and renders columns for a table", async () => {
    render(<ColumnTransformationPanel table={TABLE} />);
    await waitFor(() => expect(api.resolveColumnTransformations).toHaveBeenCalled());
    expect((await screen.findAllByText("amount_usd")).length).toBeGreaterThan(0);
  });

  it("renders the access-denied notice with SP + paths", async () => {
    (api.resolveColumnTransformations as any).mockResolvedValue({
      source: "unavailable", source_label: "LLM unavailable", columns: [],
      reason_code: "access_denied",
      denied_paths: ["/Workspace/x/nb"],
      app_service_principal: "sp-123",
    });
    render(<ColumnTransformationPanel table={TABLE} />);
    expect(await screen.findByText(/access to producer code required/i)).toBeInTheDocument();
    expect(screen.getByText("sp-123")).toBeInTheDocument();
    expect(screen.getByText("/Workspace/x/nb")).toBeInTheDocument();
  });

  it("shows the multi-producer banner and runs the comparison", async () => {
    seedProducers();
    (api.compareProducers as any).mockResolvedValue({
      table_full_name: TABLE,
      producers: [
        { key: "JOB:a", entity_type: "JOB", entity_id: "a", label: "prod_a", source: "llm" },
        { key: "JOB:b", entity_type: "JOB", entity_id: "b", label: "prod_b", source: "llm" },
      ],
      columns: [
        { column: "amount_usd", divergent: true, cells: [
          { producer: "JOB:a", present: true, expression: "amount*fx", source_columns: ["amount", "fx"], category: "ARITHMETIC" },
          { producer: "JOB:b", present: true, expression: "amount*1.1", source_columns: ["amount"], category: "ARITHMETIC" },
        ]},
      ],
      divergent_count: 1, column_count: 1,
    });
    const user = userEvent.setup();
    render(<ColumnTransformationPanel table={TABLE} />);
    expect(await screen.findByText(/2 producers write this table/i)).toBeInTheDocument();
    await user.click(screen.getByText(/compare side-by-side/i));
    await waitFor(() => expect(api.compareProducers).toHaveBeenCalled());
    // The matrix renders producer_a's divergent expression (unique to the matrix).
    expect(await screen.findByText("amount*fx")).toBeInTheDocument();
    expect(screen.getByText(/columns differ across producers/i)).toBeInTheDocument();
  });

  it("surfaces a resolve error", async () => {
    (api.resolveColumnTransformations as any).mockRejectedValue(new Error("boom"));
    render(<ColumnTransformationPanel table={TABLE} />);
    expect(await screen.findByText(/boom|failed/i)).toBeInTheDocument();
  });
});
