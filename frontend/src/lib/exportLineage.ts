import type { GraphNode, TableNode, EntityNode, LineageEdge, ColumnLineageEdge } from "../api/client";
import { downloadXlsx, type Sheet, type Cell, type CellValue, type StyleName, type Column } from "./xlsxWriter";

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

const STATUS_STYLE: Record<string, StyleName> = {
  orphan: "orphan",
  root: "root",
  leaf: "leaf",
  connected: "connected",
};

/** Build a styled sheet: header columns with auto-fit widths + data rows. */
function tableSheet(
  name: string,
  spec: { key: string; header: string; min?: number; max?: number; style?: (row: Record<string, CellValue>) => StyleName | undefined }[],
  rows: Record<string, CellValue>[]
): Sheet {
  const columns: Column[] = spec.map((c) => {
    let w = c.header.length;
    for (const r of rows) {
      const v = r[c.key];
      if (v != null) w = Math.max(w, String(v).length);
    }
    return { header: c.header, width: Math.min(c.max ?? 50, Math.max(c.min ?? 10, w + 2)) };
  });
  const body: Cell[][] = rows.map((r) =>
    spec.map((c) => {
      const v = r[c.key] ?? "";
      const style = c.style?.(r);
      return style ? { v, s: style } : v;
    })
  );
  // Keep a visible placeholder row when empty so the sheet isn't blank.
  return { name, columns, rows: body.length ? body : [spec.map(() => "")] };
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
 * Build a polished multi-sheet .xlsx workbook from the lineage graph currently
 * in the store and trigger a browser download. Sheets: Summary, Tables,
 * Lineage, Pipelines, and (when present) Column Lineage.
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

  const orphanCount = tableNodes.filter((t) => t.lineage_status === "orphan").length;

  const sheets: Sheet[] = [];

  // --- Summary (styled title + label/value pairs) ---
  const L = (v: string): Cell => ({ v, s: "label" });
  sheets.push({
    name: "Summary",
    rows: [
      [{ v: "Lineage Explorer — export", s: "title" }],
      [],
      [L("Scope"), scope],
      [L("Target"), { v: scopeLabel, s: "mono" }],
      [L("Generated"), new Date().toLocaleString()],
      [],
      [L("Tables"), tableNodes.length],
      [L("Pipelines / entities"), entityNodes.length],
      [L("Lineage edges (table → table)"), tableEdges.length],
      [L("Column lineage edges"), columnEdges.length],
      [L("Tables without lineage (orphan)"), orphanCount],
    ],
  });

  // --- Tables ---
  sheets.push(
    tableSheet(
      "Tables",
      [
        { key: "Full name", header: "Full name", min: 30, max: 60, style: () => "mono" },
        { key: "Name", header: "Name", min: 16, max: 36 },
        { key: "Catalog", header: "Catalog", min: 12 },
        { key: "Schema", header: "Schema", min: 12 },
        { key: "Type", header: "Type", min: 12 },
        { key: "Owner", header: "Owner", min: 18, max: 36 },
        { key: "Upstream", header: "Upstream", min: 9 },
        { key: "Downstream", header: "Downstream", min: 11 },
        { key: "Status", header: "Status", min: 11, style: (r) => STATUS_STYLE[String(r.Status)] },
        { key: "Columns", header: "Columns", min: 8 },
        { key: "Comment", header: "Comment", min: 20, max: 60 },
        { key: "Created", header: "Created", min: 18, max: 26 },
        { key: "Updated", header: "Updated", min: 18, max: 26 },
      ],
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
      [
        { key: "Source", header: "Source", min: 30, max: 60, style: () => "mono" },
        { key: "Target", header: "Target", min: 30, max: 60, style: () => "mono" },
      ],
      tableEdges.map((e) => ({ Source: e.source, Target: e.target }))
    )
  );

  // --- Pipelines / entities ---
  if (entityNodes.length) {
    sheets.push(
      tableSheet(
        "Pipelines",
        [
          { key: "Type", header: "Type", min: 12 },
          { key: "Name", header: "Name", min: 24, max: 50 },
          { key: "Entity ID", header: "Entity ID", min: 24, max: 44, style: () => "mono" },
          { key: "Last run", header: "Last run", min: 20, max: 28 },
          { key: "Owner", header: "Owner", min: 18, max: 36 },
          { key: "Cost", header: "Cost (USD, 30d)", min: 14, style: (r) => (r.Cost !== "" ? "cost" : undefined) },
        ],
        entityNodes.map((en) => ({
          Type: en.entity_type,
          Name: en.display_name ?? "",
          "Entity ID": en.entity_id,
          "Last run": en.last_run ?? "",
          Owner: en.owner ?? "",
          Cost: en.cost_usd ?? "",
        }))
      )
    );
  }

  // --- Column lineage (only when loaded) ---
  if (columnEdges.length) {
    sheets.push(
      tableSheet(
        "Column Lineage",
        [
          { key: "Source table", header: "Source table", min: 28, max: 54, style: () => "mono" },
          { key: "Source column", header: "Source column", min: 18, max: 36 },
          { key: "Target table", header: "Target table", min: 28, max: 54, style: () => "mono" },
          { key: "Target column", header: "Target column", min: 18, max: 36 },
        ],
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
