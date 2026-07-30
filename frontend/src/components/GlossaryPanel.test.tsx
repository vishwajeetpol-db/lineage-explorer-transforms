import { describe, it, expect, vi, afterEach, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GlossaryPanel } from "./GlossaryPanel";

function routeFetch(handlers: Record<string, any>) {
  return vi.fn().mockImplementation((url: string) => {
    for (const key of Object.keys(handlers)) {
      if (url.includes(key)) return Promise.resolve({ ok: true, json: async () => handlers[key] });
    }
    return Promise.resolve({ ok: true, json: async () => ({}) });
  });
}

const term = { term_id: "t1", name: "Revenue", definition: "money in", domain: "Finance", owner: "cfo", status: "approved", synonyms: "" };
const domain = { domain_id: "d1", name: "Finance", description: "money things", owner: "cfo", color: "#123456" };
const kpi = { kpi_id: "k1", name: "MRR", definition: "monthly recurring", formula_sql: "SELECT 1", source_tables: "t", owner: "o", domain: "Finance", granularity: "monthly" };

function fullFetch(overrides: Record<string, any> = {}) {
  return routeFetch({
    "glossary/terms": { terms: [term] },
    "glossary/domains": { domains: [domain] },
    "glossary/kpis": { kpis: [kpi] },
    ...overrides,
  });
}

describe("GlossaryPanel", () => {
  beforeEach(() => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it("loads and shows terms by default", async () => {
    global.fetch = fullFetch() as any;
    render(<GlossaryPanel />);
    expect(await screen.findByText("Revenue")).toBeInTheDocument();
    expect(screen.getByText("approved")).toBeInTheDocument();
  });

  it("switches to domains tab", async () => {
    global.fetch = fullFetch() as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("Domains"));
    expect(await screen.findByText("money things")).toBeInTheDocument();
  });

  it("switches to kpis tab", async () => {
    global.fetch = fullFetch() as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("KPIs"));
    expect(await screen.findByText("MRR")).toBeInTheDocument();
    expect(screen.getByText("SELECT 1")).toBeInTheDocument();
  });

  it("toggles the add-term form and submits", async () => {
    const fetchMock = fullFetch();
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("+ Add Term"));
    await user.type(screen.getByPlaceholderText("Term name"), "New");
    await user.click(screen.getByText("Save Term"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => c[1]?.method === "POST" && String(c[0]).includes("glossary/terms"))).toBe(true);
    });
  });

  it("deletes a term", async () => {
    const fetchMock = fullFetch();
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("×"));
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => c[1]?.method === "DELETE")).toBe(true);
    });
  });

  it("shows empty terms state", async () => {
    global.fetch = fullFetch({ "glossary/terms": { terms: [] } }) as any;
    render(<GlossaryPanel />);
    expect(await screen.findByText(/No terms found/)).toBeInTheDocument();
  });

  it("searches terms on Enter", async () => {
    const fetchMock = fullFetch();
    global.fetch = fetchMock as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    const search = screen.getByPlaceholderText("Search terms...");
    await user.type(search, "rev{Enter}");
    await waitFor(() => {
      expect(fetchMock.mock.calls.some((c) => String(c[0]).includes("q=rev"))).toBe(true);
    });
  });

  it("edits all add-term form fields", async () => {
    global.fetch = fullFetch() as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("+ Add Term"));
    await user.type(screen.getByPlaceholderText("Term name"), "N");
    await user.type(screen.getByPlaceholderText("Domain"), "D");
    await user.type(screen.getByPlaceholderText("Owner"), "O");
    await user.type(screen.getByPlaceholderText("Definition"), "def");
    await user.selectOptions(screen.getByRole("combobox"), "approved");
    expect((screen.getByPlaceholderText("Term name") as HTMLInputElement).value).toBe("N");
    // toggle form closed via Cancel
    await user.click(screen.getByText("Cancel"));
    expect(screen.queryByPlaceholderText("Term name")).not.toBeInTheDocument();
  });

  it("shows empty domains and kpis states", async () => {
    global.fetch = fullFetch({ "glossary/domains": { domains: [] }, "glossary/kpis": { kpis: [] } }) as any;
    const user = userEvent.setup();
    render(<GlossaryPanel />);
    await screen.findByText("Revenue");
    await user.click(screen.getByText("Domains"));
    expect(await screen.findByText("No domains defined yet.")).toBeInTheDocument();
    await user.click(screen.getByText("KPIs"));
    expect(await screen.findByText("No KPIs defined yet.")).toBeInTheDocument();
  });
});
