const BASE = "/api";

let _liveMode = false;
export function setLiveMode(live: boolean) { _liveMode = live; }
export function getLiveMode() { return _liveMode; }

function appendLive(url: string): string {
  if (!_liveMode) return url;
  return url + (url.includes("?") ? "&" : "?") + "live=true";
}

async function fetchJson<T>(url: string, signal?: AbortSignal): Promise<T> {
  const res = await fetch(appendLive(url), { signal });
  if (!res.ok) {
    const err = await res.text();
    throw new Error(`API error ${res.status}: ${err}`);
  }
  return res.json();
}

export interface TableNode {
  node_type: "table";
  id: string;
  name: string;
  full_name: string;
  table_type: string;
  owner: string | null;
  comment: string | null;
  columns: { name: string; type: string; nullable: boolean }[];
  created_at: string | null;
  updated_at: string | null;
  upstream_count: number;
  downstream_count: number;
  lineage_status: "connected" | "root" | "leaf" | "orphan";
}

export interface EntityNode {
  node_type: "entity";
  id: string;
  entity_type: "JOB" | "NOTEBOOK" | "PIPELINE" | "QUERY" | string;
  entity_id: string;
  display_name: string | null;
  last_run: string | null;
  owner: string | null;
  cost_usd: number | null;
}

export type GraphNode = TableNode | EntityNode;

export interface LineageEdge {
  source: string;
  target: string;
}

export interface ColumnLineageEdge {
  source_table: string;
  source_column: string;
  target_table: string;
  target_column: string;
}

export interface LineageResponse {
  nodes: GraphNode[];
  edges: LineageEdge[];
  cached?: boolean;
  cached_at?: string | null;
  cache_expires_at?: string | null;
  fetch_duration_ms?: number | null;
}

export interface ColumnLineageResponse {
  edges: ColumnLineageEdge[];
}

export interface TableSearchItem {
  name: string;
  fqdn: string;
  catalog: string;
  schema: string;
  table_type: string;
}

export interface AdminStatus {
  system: { uptime_sec: number; uptime_human: string; python_version: string; pid: number };
  memory: { rss_mb: number; vms_mb: number; rss_percent: number };
  latency: { p50_ms: number; p95_ms: number; p99_ms: number; sample_count: number };
  requests: { total: number; rate_per_min: number };
  thread_pool: { max_workers: number | string; inflight_cache_keys: string[] };
  cache: { entries: number; max_entries: number; max_memory_mb: number; ttl_seconds: number; utilization_percent: number; total_size_mb: number; inventory: { key: string; cached_at: string; last_accessed: string; last_accessed_ago: string; ttl_remaining_sec: number; expired: boolean; size_kb: number }[]; inventory_note: string };
  user_cache: { entries: number; max_entries: number };
}

export interface UserInfo {
  email: string | null;
  isAdmin: boolean;
}

export const api = {
  getUserInfo: () => fetchJson<UserInfo>(`${BASE}/user-info`),

  getTables: () => fetchJson<{ tables: TableSearchItem[] }>(`${BASE}/tables`),

  getCatalogs: () => fetchJson<{ catalogs: string[] }>(`${BASE}/catalogs`),

  getSchemas: (catalog: string) =>
    fetchJson<{ schemas: string[] }>(`${BASE}/schemas?catalog=${encodeURIComponent(catalog)}`),

  getLineage: (catalog: string, schema: string, signal?: AbortSignal) =>
    fetchJson<LineageResponse>(
      `${BASE}/lineage?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`,
      signal,
    ),

  // Catalog-wide lineage — omit schema to span every schema in the catalog.
  getCatalogLineage: (catalog: string, signal?: AbortSignal) =>
    fetchJson<LineageResponse>(
      `${BASE}/lineage?catalog=${encodeURIComponent(catalog)}`,
      signal,
    ),

  getColumnLineage: (catalog: string, schema: string, table: string, column: string) =>
    fetchJson<ColumnLineageResponse>(
      `${BASE}/column-lineage?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}&column=${encodeURIComponent(column)}`
    ),

  getSchemaColumnLineage: (catalog: string, schema: string) =>
    fetchJson<ColumnLineageResponse>(
      `${BASE}/schema-column-lineage?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`
    ),

  getEntityName: (entityType: string, entityId: string) =>
    fetchJson<{ name: string; owner?: string }>(
      `${BASE}/entity-name?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}`
    ),

  getAdminStatus: () => fetchJson<AdminStatus>(`${BASE}/admin/status`),
};
