import type { GraphNode, TableNode, EntityNode, LineageEdge, ColumnLineageEdge } from "../api/client";
import { downloadXlsx, type Sheet, type CellValue } from "./xlsxWriter";

const isEntityId = (id: string) => id.startsWith("entity:");

/**
 * Collapse entity-mediated edges (table → pipeline → table) into direct
 * table → table edges, mirroring the canvas "Tables" view. Pure table→table
 * edges pass through unchanged.
 */
function collapseToTableEdges(edges: LineageEdge[]): { source: string; target: string }[] {
  const entitySources = new Map<string, Set<string>>(); // entity → upstream tables
  const entityTargets = new Map<string, Set<string>>(); // entity → downstream tables
  const seen = new Set<string>();
  const out: { source: string; target: string }[] = [];

  const add = (source: string, target: string) => {
    if (source === target) return;
    const k = `${source}|${target}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ source, target });
  };

  for (const e of edges) {
    const se = isEntityId(e.source);
    const te = isEntityId(e.target);
    if (!se && !te) {
      add(e.source, e.target);
    } else if (!se && te) {
      if (!entitySources.has(e.target)) entitySources.set(e.target, new Set());
      entitySources.get(e.target)!.add(e.source);
    } else if (se && !te) {
      if (!entityTargets.has(e.source)) entityTargets.set(e.source, new Set());
      entityTargets.get(e.source)!.add(e.target);
    }
  }

  for (const [entity, sources] of entitySources) {
    const targets = entityTargets.get(entity);
    if (!targets) continue;
    for (const s of sources) for (const t of targets) add(s, t);
  }
  return out;
}

function splitFqdn(fullName: string): { catalog: string; schema: string } {
  const parts = fullName.split(".");
  if (parts.length === 3) return { catalog: parts[0], schema: parts[1] };
  return { catalog: "", schema: "" };
}

function safeFileLabel(s: string): string {
  return s.replace(/[^A-Za-z0-9._-]/g, "_").slice(0, 60);
}

/** A sheet built from a header row + object rows, in a fixed column order. */
function tableSheet(name: string, columns: string[], rows: Record<string, CellValue>[]): Sheet {
  const body = rows.length
    ? rows.map((r) => columns.map((c) => r[c] ?? ""))
    : [columns.map(() => "")];
  return { name, rows: [columns, ...body] };
}

export interface ExportInput {
  nodes: GraphNode[];
  edges: LineageEdge[];
  columnEdges: ColumnLineageEdge[];
  scope: "table" | "schema" | "catalog";
  catalog: string;
  schema: string;
  focusTable: string | null;
}

/**
 * Build a multi-sheet .xlsx workbook from the lineage graph currently in the
 * store and trigger a browser download. Sheets: Summary, Tables, Lineage,
 * Pipelines, and (when present) Column Lineage.
 */
export function exportLineageToExcel(input: ExportInput): void {
  const { nodes, edges, columnEdges, scope, catalog, schema, focusTable } = input;

  const tableNodes = nodes.filter((n): n is TableNode => n.node_type === "table");
  const entityNodes = nodes.filter((n): n is EntityNode => n.node_type === "entity");
  const tableEdges = collapseToTableEdges(edges);

  const scopeLabel =
    scope === "table" ? (focusTable ?? "table")
    : scope === "schema" ? `${catalog}.${schema}`
    : catalog;

  const sheets: Sheet[] = [];

  // --- Summary ---
  sheets.push({
    name: "Summary",
    rows: [
      ["Lineage Explorer — export"],
      [],
      ["Scope", scope],
      ["Target", scopeLabel],
      ["Generated", new Date().toLocaleString()],
      [],
      ["Tables", tableNodes.length],
      ["Pipelines / entities", entityNodes.length],
      ["Lineage edges (table → table)", tableEdges.length],
      ["Column lineage edges", columnEdges.length],
    ],
  });

  // --- Tables ---
  sheets.push(
    tableSheet(
      "Tables",
      ["Full name", "Name", "Catalog", "Schema", "Type", "Owner", "Upstream", "Downstream", "Status", "Columns", "Comment", "Created", "Updated"],
      tableNodes.map((t) => {
        const { catalog: cat, schema: sch } = splitFqdn(t.full_name);
        return {
          "Full name": t.full_name,
          Name: t.name,
          Catalog: cat,
          Schema: sch,
          Type: t.table_type,
          Owner: t.owner ?? "",
          Upstream: t.upstream_count,
          Downstream: t.downstream_count,
          Status: t.lineage_status,
          Columns: t.columns?.length ?? 0,
          Comment: t.comment ?? "",
          Created: t.created_at ?? "",
          Updated: t.updated_at ?? "",
        };
      })
    )
  );

  // --- Lineage (table → table) ---
  sheets.push(
    tableSheet(
      "Lineage",
      ["Source", "Target"],
      tableEdges.map((e) => ({ Source: e.source, Target: e.target }))
    )
  );

  // --- Pipelines / entities ---
  if (entityNodes.length) {
    sheets.push(
      tableSheet(
        "Pipelines",
        ["Type", "Name", "Entity ID", "Last run", "Owner", "Cost (USD, 30d)"],
        entityNodes.map((en) => ({
          Type: en.entity_type,
          Name: en.display_name ?? "",
          "Entity ID": en.entity_id,
          "Last run": en.last_run ?? "",
          Owner: en.owner ?? "",
          "Cost (USD, 30d)": en.cost_usd ?? "",
        }))
      )
    );
  }

  // --- Column lineage (only when loaded) ---
  if (columnEdges.length) {
    sheets.push(
      tableSheet(
        "Column Lineage",
        ["Source table", "Source column", "Target table", "Target column"],
        columnEdges.map((c) => ({
          "Source table": c.source_table,
          "Source column": c.source_column,
          "Target table": c.target_table,
          "Target column": c.target_column,
        }))
      )
    );
  }

  const stamp = new Date().toISOString().slice(0, 10);
  downloadXlsx(sheets, `lineage_${safeFileLabel(scopeLabel)}_${stamp}.xlsx`);
}
