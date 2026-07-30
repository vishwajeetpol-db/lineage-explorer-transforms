import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import MLModelsPanel from "./MLModelsPanel";
import { api, type MlModel } from "../../api/client";

vi.mock("../../api/client", () => ({
  api: { getMlModelsForTable: vi.fn() },
  setLiveMode: vi.fn(),
  getLiveMode: () => false,
}));

const TABLE = "cat.schema.tbl";

const models: MlModel[] = [
  {
    model_name: "churn_model",
    model_version: "3",
    job_id: null,
    run_id: "run-abcdef123456789",
    notebook_path: "/Repos/train.py",
    registered_by: "alice@x.com",
    endpoints: ["churn-endpoint"],
  },
  {
    // no version, no notebook, no endpoints, no registered_by/run_id
    model_name: "bare_model",
    model_version: "",
    job_id: null,
    run_id: null,
    notebook_path: null,
    endpoints: [],
  },
];

describe("MLModelsPanel", () => {
  beforeEach(() => {
    (api.getMlModelsForTable as any).mockReset();
  });

  it("renders NoTable when no table selected", () => {
    render(<MLModelsPanel table={null} />);
    expect(screen.getByText(/Select a table from the tree/)).toBeInTheDocument();
  });

  it("shows loading state then data", async () => {
    let resolve: (v: { models: MlModel[] }) => void = () => {};
    (api.getMlModelsForTable as any).mockReturnValue(new Promise((r) => { resolve = r; }));
    render(<MLModelsPanel table={TABLE} />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    resolve({ models });
    expect(await screen.findByText("churn_model")).toBeInTheDocument();
  });

  it("renders full model data incl. version, notebook, endpoints, metadata", async () => {
    (api.getMlModelsForTable as any).mockResolvedValue({ models });
    render(<MLModelsPanel table={TABLE} />);
    expect(await screen.findByText("churn_model")).toBeInTheDocument();
    // count line (plural)
    expect(screen.getByText(/2 models trained on this table/)).toBeInTheDocument();
    expect(screen.getByText("v3")).toBeInTheDocument();
    expect(screen.getByText("/Repos/train.py")).toBeInTheDocument();
    expect(screen.getByText("churn-endpoint")).toBeInTheDocument();
    expect(screen.getByText(/served by:/)).toBeInTheDocument();
    expect(screen.getByText("by alice@x.com")).toBeInTheDocument();
    expect(screen.getByText(/run run-abcdef12/)).toBeInTheDocument();
    // bare model: no endpoint => "Not currently served"
    expect(screen.getByText("bare_model")).toBeInTheDocument();
    expect(screen.getByText("Not currently served by an endpoint")).toBeInTheDocument();
  });

  it("renders singular count for a single model", async () => {
    (api.getMlModelsForTable as any).mockResolvedValue({ models: [models[0]] });
    render(<MLModelsPanel table={TABLE} />);
    expect(await screen.findByText(/1 model trained on this table/)).toBeInTheDocument();
  });

  it("renders empty state when no models", async () => {
    (api.getMlModelsForTable as any).mockResolvedValue({ models: [] });
    render(<MLModelsPanel table={TABLE} />);
    expect(await screen.findByText("No ML models trained on this table.")).toBeInTheDocument();
  });

  it("shows error state when the api rejects", async () => {
    (api.getMlModelsForTable as any).mockRejectedValue(new Error("ml boom"));
    render(<MLModelsPanel table={TABLE} />);
    expect(await screen.findByText("ml boom")).toBeInTheDocument();
  });

  it("shows fallback error when rejection lacks a message", async () => {
    (api.getMlModelsForTable as any).mockRejectedValue({});
    render(<MLModelsPanel table={TABLE} />);
    expect(await screen.findByText("Failed to load ML models")).toBeInTheDocument();
  });

  it("re-fetches when the table prop changes", async () => {
    (api.getMlModelsForTable as any).mockResolvedValue({ models });
    const { rerender } = render(<MLModelsPanel table={TABLE} />);
    await screen.findByText("churn_model");
    expect(api.getMlModelsForTable).toHaveBeenCalledWith("cat", "schema", "tbl");
    rerender(<MLModelsPanel table="cat2.s2.t2" />);
    await waitFor(() => expect(api.getMlModelsForTable).toHaveBeenCalledWith("cat2", "s2", "t2"));
  });
});
