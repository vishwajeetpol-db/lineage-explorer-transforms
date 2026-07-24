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
  lineage_window_days?: number | null;
  truncated?: boolean;
  graph_warnings?: Record<string, unknown> | null;
}

export interface SystemHealth {
  system_tables_available: boolean;
  sp_grants_valid: boolean;
  missing_grants: string[];
}

export interface HealthResponse {
  status: string;
  version: string;
  system_health: SystemHealth;
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

// --- Delta Sharing overlay ---
export type SharingAudience = "provider" | "recipient" | "both";

export interface SharedOutEntry {
  full_name: string;
  share_name: string;
  recipients: string[];
  shared_as: string | null;
  cdf_enabled: boolean;
}

export interface ForeignCatalogEntry {
  catalog_name: string;
  provider_name: string;
  share_names: string[];
  cloud: string | null;
  region: string | null;
}

export interface SharingOverlay {
  audience: SharingAudience;
  shared_out: SharedOutEntry[];
  foreign_catalogs: ForeignCatalogEntry[];
  available: boolean;
}

export interface SharingOverview {
  shares: { share_name: string; owner: string | null; comment: string | null; num_tables: number; recipients: string[] }[];
  recipients: { recipient_name: string; authentication_type: string | null; owner: string | null; comment: string | null }[];
  providers: { provider_name: string; cloud: string | null; region: string | null; comment: string | null }[];
  foreign_catalogs: { catalog_name: string; provider_name: string | null; share_names: string[] }[];
  shared_tables: { full_name: string; share_name: string }[];
  totals: { shares: number; recipients: number; providers: number; foreign_catalogs: number; shared_tables: number };
}

// --- Table Lineage workspace capabilities ---

export interface ConsumerEntity {
  entity_type: string;
  entity_id: string;
  display_name?: string;
  deep_link?: string | null;
}

export interface ImpactResponse {
  table_full_name: string;
  max_hops: number;
  lookback_days: number;
  downstream_count: number;
  consumer_owners: string[];
  consumers?: {
    by_type: Record<string, number>;
    total: number;
    entities: ConsumerEntity[];
  };
  sensitive_affected_count: number;
  sensitive_affected: string[];
  downstream_tables: {
    full_name: string;
    hop_distance: number;
    owner: string | null;
    has_sensitive_columns: boolean;
  }[];
}

export interface GovernanceColumn {
  name: string;
  type: string;
  nullable: string;
  comment: string | null;
  sensitivity: string | null;
  sensitivity_source: string | null;
}

export interface GovernanceResponse {
  table_full_name: string;
  owner: string | null;
  table_type: string | null;
  created_by: string | null;
  created_at: string | null;
  last_altered_by: string | null;
  last_altered_at: string | null;
  comment: string | null;
  tags: { name: string; value: string | null }[];
  columns: GovernanceColumn[];
  sensitive_columns: { column: string; sensitivity: string; source: string }[];
  config_rules_applied: number;
}

export interface GovernanceRule {
  rule_id: string;
  catalog: string | null;
  schema: string | null;
  table_pattern: string | null;
  column_pattern: string | null;
  tag_name: string | null;
  sensitivity: string;
  owner: string | null;
  created_at: string | null;
  updated_at: string | null;
  notes: string | null;
}

export interface GovernanceRuleInput {
  rule_id?: string;
  catalog?: string;
  schema_name?: string;
  table_pattern?: string;
  column_pattern?: string;
  tag_name?: string;
  sensitivity: string;
  notes?: string;
}

export interface AccessResponse {
  table_full_name: string;
  lookback_days: number;
  identities: {
    owner: string | null;
    created_by: string | null;
    created_at: string | null;
    last_altered_by: string | null;
    last_altered_at: string | null;
  };
  declared_grants: { principal: string; privilege: string; granted_by?: string | null; object_type?: string; [k: string]: unknown }[];
  audit_access: { user_email: string; action_name?: string; access_count?: number; last_accessed_at?: string; [k: string]: unknown }[];
  recent_events: { event_time: string; action_name: string | null; user_email: string | null; source_ip: string | null }[];
  dormant_grants: string[];
  unique_empirical_users: number;
  grantee_count: number;
  read_count: number;
  write_count: number;
}

export type ProducerHealthStatus = "failed" | "stale" | "healthy" | "no_history";

export interface ProducerHealth {
  entity_type: string;
  entity_id: string;
  status: ProducerHealthStatus;
  last_result: string | null;
  last_run_at: string | null;
  success_rate: number | null;
}

export interface FlaggedTable {
  table: string;
  short_name: string;
  hop: number;
  is_focus: boolean;
  status: ProducerHealthStatus;
  producers: ProducerHealth[];
}

export interface RootCauseTrace {
  focus_table: string;
  max_hops: number;
  lookback_days: number;
  stale_days: number;
  counts: { failed: number; stale: number; healthy: number; no_history: number };
  prime_suspect: FlaggedTable | null;
  failure_path: { table: string; short_name: string; hop: number; status: ProducerHealthStatus }[];
  flagged: FlaggedTable[];
}

export interface MlModel {
  model_name: string;
  model_version: string;
  job_id: string | null;
  run_id: string | null;
  notebook_path: string | null;
  registered_by?: string | null;
  endpoints?: string[];
  [k: string]: unknown;
}

export interface AnalyzeProducerColumn {
  column?: string;
  target_column?: string;
  expression?: string;
  transformation?: string;
  source_columns?: string[];
  category?: string;
  confidence?: number | string;
  [k: string]: unknown;
}

export interface AnalyzeProducerResponse {
  source: "stored" | "llm" | "unavailable";
  entity_type: string;
  entity_id: string;
  target_table: string;
  columns: AnalyzeProducerColumn[];
  source_hash: string | null;
  llm_model: string | null;
  version: number | null;
  versions: AnalysisVersion[];
  stale: boolean;
  analyzed_at?: string;
  analyzed_by?: string;
  detail?: string;
}

export interface AnalysisVersion {
  version: number;
  llm_model: string | null;
  source_hash: string | null;
  analyzed_at: string;
  analyzed_by: string | null;
  target_table?: string;
}

export interface AnalysisVersionFull {
  entity_type: string;
  entity_id: string;
  source_hash: string | null;
  source_code: string | null;
  target_table: string;
  columns: AnalyzeProducerColumn[];
  llm_model: string | null;
  analyzed_at: string;
  analyzed_by: string | null;
  version: number;
}

export interface AnalysisCompare {
  from: AnalysisVersionFull;
  to: AnalysisVersionFull;
  source_changed: boolean;
  changed_count: number;
  column_diffs: {
    column: string;
    status: "added" | "removed" | "changed" | "unchanged";
    from: AnalyzeProducerColumn | null;
    to: AnalyzeProducerColumn | null;
  }[];
}

export const api = {
  getUserInfo: () => fetchJson<UserInfo>(`${BASE}/user-info`),

  // Impact analysis (blast radius) — cap 18.
  getImpact: (catalog: string, schema: string, table: string, maxHops?: number) =>
    fetchJson<ImpactResponse>(
      `${BASE}/impact?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (maxHops ? `&max_hops=${maxHops}` : "")
    ),

  // Governance & classification — cap 17.
  getGovernance: (catalog: string, schema: string, table: string) =>
    fetchJson<GovernanceResponse>(
      `${BASE}/governance?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}`
    ),

  // Governance classification rules — cap 17.
  listGovernanceRules: () => fetchJson<{ rules: GovernanceRule[] }>(`${BASE}/governance/config`),

  upsertGovernanceRule: async (rule: GovernanceRuleInput) => {
    const res = await fetch(`${BASE}/governance/config`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(rule),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<{ rule_id: string; status: string }>;
  },

  deleteGovernanceRule: async (ruleId: string) => {
    const res = await fetch(`${BASE}/governance/config?rule_id=${encodeURIComponent(ruleId)}`, {
      method: "DELETE",
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<{ rule_id: string; status: string }>;
  },

  // Access & security lineage — cap 20.
  getAccess: (catalog: string, schema: string, table: string) =>
    fetchJson<AccessResponse>(
      `${BASE}/access?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}`
    ),

  // Health-based root-cause trace for a table — cap 09.
  getRootCauseTrace: (catalog: string, schema: string, table: string, maxHops?: number) =>
    fetchJson<RootCauseTrace>(
      `${BASE}/root-cause/trace?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (maxHops ? `&max_hops=${maxHops}` : "")
    ),

  // ML models trained on a UC table — cap 21.
  getMlModelsForTable: (catalog: string, schema: string, table: string) =>
    fetchJson<{ models: MlModel[] }>(
      `${BASE}/ml/models-for-table?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}`
    ),

  // LLM producer source-code analysis — cap 27.
  analyzeProducer: async (body: {
    entity_type: string;
    entity_id: string;
    target_table: string;
    force_rerun?: boolean;
    target_columns?: string[];
    model?: string;
  }) => {
    const res = await fetch(`${BASE}/analyze-producer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<AnalyzeProducerResponse>;
  },

  // Available LLM serving endpoints for the model dropdown.
  getAnalyzeModels: () => fetchJson<{ models: string[]; default: string }>(`${BASE}/analyze-producer/models`),

  // A specific stored analysis version (full — incl. source snapshot).
  getAnalysisVersion: (entityType: string, entityId: string, targetTable: string, version: number) =>
    fetchJson<AnalysisVersionFull>(
      `${BASE}/analyze-producer/version?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}&target_table=${encodeURIComponent(targetTable)}&version=${version}`
    ),

  // Compare two stored versions (code + per-column diff).
  compareAnalysisVersions: (entityType: string, entityId: string, targetTable: string, fromV: number, toV: number) =>
    fetchJson<AnalysisCompare>(
      `${BASE}/analyze-producer/compare?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}&target_table=${encodeURIComponent(targetTable)}&from_version=${fromV}&to_version=${toV}`
    ),

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

  // End-to-end cross-catalog trace from a single seed table (catalog.schema.table).
  getLineageTrace: (table: string, signal?: AbortSignal) =>
    fetchJson<LineageResponse>(`${BASE}/lineage/trace?table=${encodeURIComponent(table)}`, signal),

  getColumnLineage: (catalog: string, schema: string, table: string, column: string) =>
    fetchJson<ColumnLineageResponse>(
      `${BASE}/column-lineage?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}&column=${encodeURIComponent(column)}`
    ),

  getSchemaColumnLineage: (catalog: string, schema: string) =>
    fetchJson<ColumnLineageResponse>(
      `${BASE}/schema-column-lineage?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}`
    ),

  // Server-side .xlsx export URL. Omit schema for catalog-wide scope.
  lineageExportUrl: (catalog: string, schema?: string) =>
    `${BASE}/lineage/export?catalog=${encodeURIComponent(catalog)}` +
    (schema ? `&schema=${encodeURIComponent(schema)}` : ""),

  // Delta Sharing overlay for a lineage scope (omit schema for catalog-wide).
  getSharingOverlay: (catalog: string, schema: string | undefined, audience: SharingAudience) =>
    fetchJson<SharingOverlay>(
      `${BASE}/sharing/overlay?catalog=${encodeURIComponent(catalog)}` +
      (schema ? `&schema=${encodeURIComponent(schema)}` : "") +
      `&audience=${encodeURIComponent(audience)}`
    ),

  getSharingOverview: () => fetchJson<SharingOverview>(`${BASE}/sharing/overview`),

  getEntityName: (entityType: string, entityId: string) =>
    fetchJson<{ name: string; owner?: string }>(
      `${BASE}/entity-name?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}`
    ),

  getHealth: () => fetchJson<HealthResponse>(`/health`),

  getAdminStatus: () => fetchJson<AdminStatus>(`${BASE}/admin/status`),

  /** Invalidate transformation lineage. scope: cache (flush in-memory) | all (wipe stored) | table. */
  invalidateTransform: async (scope: "cache" | "all" | "table", tableFqn?: string) => {
    const q = new URLSearchParams({ scope });
    if (tableFqn) q.set("table_fqn", tableFqn);
    const res = await fetch(`${BASE}/transform/invalidate?${q.toString()}`, { method: "POST" });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<{ status: string; scope: string; cleared?: string[] }>;
  },
};
