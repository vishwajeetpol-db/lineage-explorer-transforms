/**
 * Business view — a plain-language lens over the technical lineage graph.
 *
 * The default ("technical") view is built for data engineers: fully-qualified
 * names, catalog/schema/table types, job & pipeline IDs, column-level detail.
 * The business view keeps the SAME underlying graph but relabels everything into
 * terms a non-engineer understands, humanizes snake_case names, and derives a
 * one-line description from data that is ALREADY loaded (table comments,
 * relationship counts, entity display names) — so the toggle is instant and
 * needs no backend call. Richer LLM-generated narration can be layered on later.
 *
 * Everything here is pure and deterministic so it is trivially unit-tested and
 * safe to call inside a React render.
 */
import type { GraphNode } from "../api/client";

// Technical entity_type → business label. Anything unrecognized is a "Process"
// (the safest generic term for "a thing that moves/transforms data").
const ENTITY_LABELS: Record<string, string> = {
  JOB: "Process",
  PIPELINE: "Data pipeline",
  NOTEBOOK: "Code step",
  QUERY: "Query",
  DASHBOARD: "Report",
  DASHBOARD_V3: "Report",
};

export function businessEntityLabel(entityType: string): string {
  return ENTITY_LABELS[(entityType || "").toUpperCase()] || "Process";
}

// Technical table_type → business label. File/volume/path storage collapses to
// file-oriented language; everything table-shaped is a "Dataset".
const TABLE_LABELS: Record<string, string> = {
  MANAGED: "Dataset",
  TABLE: "Dataset",
  EXTERNAL: "Dataset",
  VIEW: "View",
  // A materialized view is just a stored dataset to a business user — do NOT
  // call it a "summary" (it isn't necessarily aggregated). Nuance is left to the
  // AI graph explanation.
  MATERIALIZED_VIEW: "Dataset",
  STREAMING_TABLE: "Live dataset",
  EXTERNAL_LINEAGE: "External source",
  VOLUME: "File",
  PATH: "File",
};

export function businessTableLabel(tableType: string): string {
  return TABLE_LABELS[(tableType || "").toUpperCase()] || "Dataset";
}

/**
 * snake_case / kebab-case / camelCase / dotted names → Title Case words.
 *   orders_curated            → "Orders Curated"
 *   catalog.schema.fct_sales  → "Fct Sales"
 *   customerId                → "Customer Id"
 * Short all-caps tokens (ID, USD, FX) are preserved as acronyms.
 */
export function humanizeName(name: string): string {
  if (!name) return "";
  const last = name.includes(".") ? name.split(".").pop() || name : name;
  return last
    .replace(/[_\-]+/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2") // split camelCase
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((w) =>
      w.length <= 3 && w === w.toUpperCase()
        ? w // keep short acronyms as-is
        : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()
    )
    .join(" ");
}

/** The friendly node label a business user sees (humanized name). */
export function businessNodeLabel(node: GraphNode): string {
  if (node.node_type === "entity") {
    return humanizeName(node.display_name || node.entity_id);
  }
  return humanizeName(node.name);
}

/** The friendly type label (a short noun a business user understands). */
export function businessNodeType(node: GraphNode): string {
  return node.node_type === "entity"
    ? businessEntityLabel(node.entity_type)
    : businessTableLabel(node.table_type);
}

function plural(n: number, one: string): string {
  return `${n} ${one}${n === 1 ? "" : "s"}`;
}

/**
 * One-line, plain-English description of what a node is — derived from data that
 * is already present so no network call is needed. For tables we prefer the
 * curator-written comment; otherwise we describe the table by its position in
 * the flow (how many sources feed it, how many consumers it feeds).
 */
export function businessDescription(node: GraphNode): string {
  if (node.node_type === "entity") {
    const kind = businessEntityLabel(node.entity_type).toLowerCase();
    return `A ${kind} that moves and transforms data.`;
  }
  // Table: a human-written comment is the best description when present.
  if (node.comment && node.comment.trim()) return node.comment.trim();

  const kind = businessTableLabel(node.table_type).toLowerCase();
  const up = node.upstream_count || 0;
  const down = node.downstream_count || 0;

  if (up > 0 && down > 0) {
    return `A ${kind} built from ${plural(up, "source")}, feeding ${plural(down, "downstream consumer")}.`;
  }
  if (up > 0) {
    return `A ${kind} built from ${plural(up, "source")}.`;
  }
  if (down > 0) {
    return `A source ${kind} feeding ${plural(down, "downstream consumer")}.`;
  }
  return `A standalone ${kind}.`;
}

/**
 * Entity types that are too low-level to show a business audience. In the
 * business view these are hidden and their table→table flow is preserved by the
 * canvas's edge-collapsing, so the picture stays connected without the noise.
 */
const HIDDEN_ENTITY_TYPES = new Set(["QUERY"]);

export function isHiddenInBusinessView(node: GraphNode): boolean {
  return node.node_type === "entity" && HIDDEN_ENTITY_TYPES.has((node.entity_type || "").toUpperCase());
}
