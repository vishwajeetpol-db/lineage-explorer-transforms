import { describe, it, expect } from "vitest";
import {
  businessEntityLabel,
  businessTableLabel,
  humanizeName,
  businessNodeLabel,
  businessNodeType,
  businessDescription,
  isHiddenInBusinessView,
} from "./businessView";
import type { GraphNode } from "../api/client";

function table(overrides: Partial<Extract<GraphNode, { node_type: "table" }>> = {}): GraphNode {
  return {
    node_type: "table",
    id: "cat.sch.orders_curated",
    name: "orders_curated",
    full_name: "cat.sch.orders_curated",
    table_type: "MANAGED",
    owner: null,
    comment: null,
    columns: [],
    created_at: null,
    updated_at: null,
    upstream_count: 0,
    downstream_count: 0,
    lineage_status: "connected",
    ...overrides,
  };
}

function entity(overrides: Partial<Extract<GraphNode, { node_type: "entity" }>> = {}): GraphNode {
  return {
    node_type: "entity",
    id: "entity:JOB:123",
    entity_type: "JOB",
    entity_id: "123",
    display_name: null,
    last_run: null,
    owner: null,
    cost_usd: null,
    ...overrides,
  } as GraphNode;
}

describe("businessEntityLabel", () => {
  it("maps known entity types", () => {
    expect(businessEntityLabel("JOB")).toBe("Process");
    expect(businessEntityLabel("PIPELINE")).toBe("Data pipeline");
    expect(businessEntityLabel("NOTEBOOK")).toBe("Code step");
    expect(businessEntityLabel("QUERY")).toBe("Query");
    expect(businessEntityLabel("DASHBOARD")).toBe("Report");
  });
  it("is case-insensitive and defaults to Process", () => {
    expect(businessEntityLabel("job")).toBe("Process");
    expect(businessEntityLabel("SOMETHING_ELSE")).toBe("Process");
    expect(businessEntityLabel("")).toBe("Process");
  });
});

describe("businessTableLabel", () => {
  it("maps known table types (simple & accurate)", () => {
    expect(businessTableLabel("MANAGED")).toBe("Dataset");
    expect(businessTableLabel("EXTERNAL")).toBe("Dataset");
    expect(businessTableLabel("VIEW")).toBe("View");
    // A materialized view is a stored dataset — never "summary".
    expect(businessTableLabel("MATERIALIZED_VIEW")).toBe("Dataset");
    expect(businessTableLabel("STREAMING_TABLE")).toBe("Live dataset");
    expect(businessTableLabel("VOLUME")).toBe("File");
    expect(businessTableLabel("PATH")).toBe("File");
  });
  it("defaults unknown types to Dataset", () => {
    expect(businessTableLabel("WEIRD")).toBe("Dataset");
    expect(businessTableLabel("")).toBe("Dataset");
  });
});

describe("humanizeName", () => {
  it("title-cases snake_case", () => {
    expect(humanizeName("orders_curated")).toBe("Orders Curated");
  });
  it("uses only the last dotted segment", () => {
    expect(humanizeName("cat.sch.fct_sales")).toBe("Fct Sales");
  });
  it("splits camelCase", () => {
    expect(humanizeName("customerId")).toBe("Customer Id");
  });
  it("handles kebab-case", () => {
    expect(humanizeName("daily-load")).toBe("Daily Load");
  });
  it("preserves already-uppercase short acronyms", () => {
    expect(humanizeName("USD_amount")).toBe("USD Amount");
    expect(humanizeName("FX")).toBe("FX");
    // lowercase tokens can't be known to be acronyms, so they title-case
    expect(humanizeName("fx")).toBe("Fx");
  });
  it("returns empty for empty input", () => {
    expect(humanizeName("")).toBe("");
  });
});

describe("businessNodeLabel", () => {
  it("humanizes a table name", () => {
    expect(businessNodeLabel(table())).toBe("Orders Curated");
  });
  it("humanizes an entity display name", () => {
    expect(businessNodeLabel(entity({ display_name: "nightly_etl" }))).toBe("Nightly Etl");
  });
  it("falls back to entity_id when no display name", () => {
    expect(businessNodeLabel(entity({ entity_id: "load_job" }))).toBe("Load Job");
  });
});

describe("businessNodeType", () => {
  it("returns the friendly type for tables and entities", () => {
    expect(businessNodeType(table({ table_type: "VIEW" }))).toBe("View");
    expect(businessNodeType(entity({ entity_type: "PIPELINE" }))).toBe("Data pipeline");
  });
});

describe("businessDescription", () => {
  it("prefers a table's comment when present", () => {
    expect(businessDescription(table({ comment: "  Curated orders for finance.  " })))
      .toBe("Curated orders for finance.");
  });
  it("describes a table with both upstream and downstream", () => {
    expect(businessDescription(table({ upstream_count: 2, downstream_count: 3 })))
      .toBe("A dataset built from 2 sources, feeding 3 downstream consumers.");
  });
  it("singularizes counts", () => {
    expect(businessDescription(table({ upstream_count: 1, downstream_count: 0 })))
      .toBe("A dataset built from 1 source.");
  });
  it("describes a source-only table", () => {
    expect(businessDescription(table({ upstream_count: 0, downstream_count: 1 })))
      .toBe("A source dataset feeding 1 downstream consumer.");
  });
  it("describes a standalone table", () => {
    expect(businessDescription(table({ upstream_count: 0, downstream_count: 0 })))
      .toBe("A standalone dataset.");
  });
  it("describes an entity by its friendly kind", () => {
    expect(businessDescription(entity({ entity_type: "PIPELINE" })))
      .toBe("A data pipeline that moves and transforms data.");
  });
});

describe("isHiddenInBusinessView", () => {
  it("hides QUERY entities", () => {
    expect(isHiddenInBusinessView(entity({ entity_type: "QUERY" }))).toBe(true);
  });
  it("keeps jobs, pipelines, and tables", () => {
    expect(isHiddenInBusinessView(entity({ entity_type: "JOB" }))).toBe(false);
    expect(isHiddenInBusinessView(entity({ entity_type: "PIPELINE" }))).toBe(false);
    expect(isHiddenInBusinessView(table())).toBe(false);
  });
});
