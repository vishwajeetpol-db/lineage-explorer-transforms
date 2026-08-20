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
  // Precise table→table dependencies (independent of the entity-routed `edges`),
  // used for the "datasets only" business view so collapsing entities never has
  // to cross-product an entity's inputs × outputs.
  table_edges?: LineageEdge[];
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

// Per-table capability cache metadata, attached to Impact / Root Cause /
// Governance / Access responses. Present whether served from cache or freshly
// computed; `from_cache` + `stale` drive the panel's refresh badge.
export interface CacheMeta {
  from_cache: boolean;
  cached_at: string | null;
  cached_by: string | null;
  stale: boolean;
}

// One row in the Admin capability-cache inventory.
export interface CapabilityCacheEntry {
  table_fqn: string;
  tab: string;
  cached_at: string | null;
  cached_by: string | null;
  stale: boolean;
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
  _cache?: CacheMeta;
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
  _cache?: CacheMeta;
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
  _cache?: CacheMeta;
}

// One run/update in an entity's recent-runs health check.
export interface EntityRun {
  run_id: string | null;
  result_state: string | null;
  succeeded: boolean;
  started_at: string | null;
  ended_at: string | null;
  duration_seconds: number | null;
  cost_usd: number | null;
  run_url: string | null;
}

// Health check for a JOB or PIPELINE — last N runs + summary.
export interface EntityRuns {
  entity_type: string;
  entity_id: string;
  lookback_days: number;
  runs: EntityRun[];
  verdict: "healthy" | "degraded" | "failing" | "unknown";
  success_rate: number | null;
  avg_duration_seconds: number | null;
  duration_trend: "up" | "down" | "flat" | null;
  total_cost_usd: number | null;
  cost_spike_run_id: string | null;
  entity_url: string | null;
  detail?: string;
  _cache?: CacheMeta;
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
  _cache?: CacheMeta;
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

export interface TransformVersion {
  ref: string;                       // "plan_capture:3" | "llm:5"
  source: "plan_capture" | "llm";
  version: number | null;
  label: string;
  llm_model?: string | null;
  captured_via?: string | null;
  analyzed_at?: string | null;
  analyzed_by?: string | null;
}

export interface TransformVersionDetail {
  ref: string;
  source: "plan_capture" | "llm";
  version: number | null;
  label: string;
  columns: AnalyzeProducerColumn[];
  analyzed_at?: string | null;
}

export interface CrossSourceCompare {
  from: { ref: string; source: string; version: number | null; label: string; analyzed_at?: string | null };
  to: { ref: string; source: string; version: number | null; label: string; analyzed_at?: string | null };
  cross_source: boolean;
  changed_count: number;
  column_diffs: {
    column: string;
    status: "added" | "removed" | "changed" | "unchanged";
    from: AnalyzeProducerColumn | null;
    to: AnalyzeProducerColumn | null;
  }[];
  error?: string;
}

// Multi-producer comparison: a per-column matrix across N producers of one table.
export interface ProducerCompareCell {
  producer: string;              // "JOB:123" key matching producers[].key
  present: boolean;
  expression: string | null;
  source_columns: string[];
  category: string | null;
}
export interface ProducerCompare {
  table_full_name: string;
  producers: {
    key: string; entity_type: string; entity_id: string;
    label: string; source: string | null; reason_code?: string | null; detail?: string | null;
  }[];
  columns: { column: string; divergent: boolean; cells: ProducerCompareCell[] }[];
  divergent_count: number;
  column_count: number;
  error?: string;
}

export interface ColumnTransformResult {
  table_full_name: string;
  entity_type: string | null;
  entity_id: string | null;
  columns: AnalyzeProducerColumn[];
  source: "plan_capture" | "cdc_spec" | "stored" | "llm" | "unavailable" | "none" | null;
  source_label: string | null;
  version: number | null;
  versions: AnalysisVersion[];
  stale: boolean;
  llm_model: string | null;
  captured_at?: string;
  analyzed_at?: string;
  // Plain fallback label for the producer this lineage came from (set when the
  // panel opens without a producer picked and surfaces an existing analysis).
  producer_label?: string | null;
  cdc_spec?: { keys?: unknown; sequence_by?: string; scd_type?: unknown; source?: string; version?: number };
  detail?: string | null;
  // Actionable failure metadata (present when producer source couldn't be read).
  reason_code?: "access_denied" | "entity_missing" | "no_source" | "no_columns" | "llm_error" | "llm_not_configured" | null;
  denied_paths?: string[] | null;
  app_service_principal?: string | null;
}

/** Streaming events from the metadata-driven-framework deep analysis. */
export interface DeepAnalyzeStep {
  type: "step";
  step: string;
  status: "running" | "ok" | "warn" | "error";
  message: string;
  config_tables?: string[];
  parameters?: Record<string, unknown>;
}
export interface DeepAnalyzeResultEvent {
  type: "result";
  derived: boolean;
  columns: AnalyzeProducerColumn[];
  version?: number | null;
  detail?: string;
  derived_via?: string;
  config_tables?: string[];
  // Set on a non-derived result to explain why (e.g. "config_empty" when the
  // framework's config table has no rows to derive from right now).
  reason_code?: string | null;
}
export interface DeepAnalyzeErrorEvent { type: "error"; message: string }
export type DeepAnalyzeEvent = DeepAnalyzeStep | DeepAnalyzeResultEvent | DeepAnalyzeErrorEvent;

/** A column enriched with a plain-English LLM explanation (keeps source_columns/
 *  expression/category so the UI can draw the source→transform→target graphic). */
export interface OverviewColumn extends AnalyzeProducerColumn {
  explanation?: string;
}

export interface ColumnOverviewResult {
  table_full_name: string;
  source: ColumnTransformResult["source"];
  source_label: string | null;
  version: number | null;
  summary: string;
  columns: OverviewColumn[];
  error?: string | null;
  _cache?: CacheMeta | null;
}

export interface LineageExplainStep {
  title: string;
  detail: string;
}

export interface LineageExplainResult {
  summary: string;
  steps: LineageExplainStep[];
  error?: string | null;
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

  // Impact analysis (blast radius) — cap 18. Served from the per-table
  // capability cache unless `refresh` is true.
  getImpact: (catalog: string, schema: string, table: string, maxHops?: number, refresh?: boolean) =>
    fetchJson<ImpactResponse>(
      `${BASE}/impact?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (maxHops ? `&max_hops=${maxHops}` : "") + (refresh ? "&refresh=true" : "")
    ),

  // Governance & classification — cap 17.
  getGovernance: (catalog: string, schema: string, table: string, refresh?: boolean) =>
    fetchJson<GovernanceResponse>(
      `${BASE}/governance?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (refresh ? "&refresh=true" : "")
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
  getAccess: (catalog: string, schema: string, table: string, refresh?: boolean) =>
    fetchJson<AccessResponse>(
      `${BASE}/access?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (refresh ? "&refresh=true" : "")
    ),

  // Health-based root-cause trace for a table — cap 09.
  getRootCauseTrace: (catalog: string, schema: string, table: string, maxHops?: number, refresh?: boolean) =>
    fetchJson<RootCauseTrace>(
      `${BASE}/root-cause/trace?catalog=${encodeURIComponent(catalog)}&schema=${encodeURIComponent(schema)}&table=${encodeURIComponent(table)}` +
      (maxHops ? `&max_hops=${maxHops}` : "") + (refresh ? "&refresh=true" : "")
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

  // Unified version list across sources (captured plans + LLM).
  listColumnTransformationVersions: async (body: {
    catalog: string; schema_name: string; table: string; entity_type?: string; entity_id?: string;
  }) => {
    const res = await fetch(`${BASE}/column-transformations/versions`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<{ versions: TransformVersion[] }>;
  },

  // Load one transformation version's columns by ref (plan_capture:N | llm:N).
  getTransformationVersion: async (body: {
    catalog: string; schema_name: string; table: string; ref: string; entity_type?: string; entity_id?: string;
  }) => {
    const res = await fetch(`${BASE}/column-transformations/version`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<TransformVersionDetail>;
  },

  // Compare two transformation versions from any source (captured plan / LLM).
  compareTransformationVersions: async (body: {
    catalog: string; schema_name: string; table: string;
    ref_from: string; ref_to: string; entity_type?: string; entity_id?: string;
  }) => {
    const res = await fetch(`${BASE}/column-transformations/compare`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<CrossSourceCompare>;
  },

  // Compare column transformations across multiple producers of the same table.
  compareProducers: async (body: {
    catalog: string; schema_name: string; table: string;
    producers: { entity_type: string; entity_id: string }[]; force_rerun?: boolean;
  }) => {
    const res = await fetch(`${BASE}/column-transformations/compare-producers`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<ProducerCompare>;
  },

  // Unified column-transformation lineage (captured plan → CDC → stored LLM → fresh LLM).
  resolveColumnTransformations: async (body: {
    catalog: string; schema_name: string; table: string;
    entity_type?: string; entity_id?: string; force_rerun?: boolean; model?: string;
  }) => {
    const res = await fetch(`${BASE}/column-transformations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<ColumnTransformResult>;
  },

  // Plain-English LLM overview (summary + per-column explanation), cached per
  // table. `refresh` re-runs the LLM.
  getColumnTransformationOverview: async (body: {
    catalog: string; schema_name: string; table: string;
    entity_type?: string; entity_id?: string; refresh?: boolean; model?: string;
  }) => {
    const res = await fetch(`${BASE}/column-transformations/overview`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<ColumnOverviewResult>;
  },

  // Plain-English AI explanation of the CURRENT lineage graph (Business-view
  // lightbulb). The caller posts the on-screen nodes/edges so the narrative
  // matches exactly what's shown (datasets-only vs datasets+processing).
  explainLineageGraph: async (body: {
    focus_table: string;
    nodes: { id: string; label: string; type: string }[];
    edges: { source: string; target: string }[];
    detail?: "data" | "data_and_processing";
    model?: string;
  }) => {
    const res = await fetch(`${BASE}/lineage/explain`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<LineageExplainResult>;
  },

  // Deep framework fallback — streams NDJSON commentary events; `onEvent` is
  // called for each. Resolves when the stream ends.
  deepAnalyzeColumnTransformations: async (
    body: { catalog: string; schema_name: string; table: string; entity_type: string; entity_id: string; model?: string },
    onEvent: (ev: DeepAnalyzeEvent) => void,
  ) => {
    const res = await fetch(`${BASE}/column-transformations/deep-analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok || !res.body) throw new Error(`API error ${res.status}: ${await res.text().catch(() => "")}`);
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    const flush = (line: string) => {
      const s = line.trim();
      if (!s) return;
      try { onEvent(JSON.parse(s) as DeepAnalyzeEvent); } catch { /* ignore partial/garbage */ }
    };
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = buf.indexOf("\n")) >= 0) {
        flush(buf.slice(0, idx));
        buf = buf.slice(idx + 1);
      }
    }
    flush(buf);
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

  // Health check for a JOB/PIPELINE node — last N runs + summary. Cached per
  // entity; refresh=true recomputes live.
  getEntityRuns: (entityType: string, entityId: string, limit = 5, refresh = false) =>
    fetchJson<EntityRuns>(
      `${BASE}/observability/runs?entity_type=${encodeURIComponent(entityType)}&entity_id=${encodeURIComponent(entityId)}&limit=${limit}` +
      (refresh ? "&refresh=true" : "")
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

  // -- Per-table capability cache (Impact / Root Cause / Governance / Access) --

  /** List cached (table, tab) capability entries for the Admin dashboard. */
  getCapabilityCacheInventory: () =>
    fetchJson<{ entries: CapabilityCacheEntry[]; count: number }>(`${BASE}/admin/capability-cache`),

  /** Evict capability cache. scope: entry (needs tableFqn+tab) | table (needs tableFqn) | all. */
  evictCapabilityCache: async (scope: "entry" | "table" | "all", tableFqn?: string, tab?: string) => {
    const q = new URLSearchParams({ scope });
    if (tableFqn) q.set("table_fqn", tableFqn);
    if (tab) q.set("tab", tab);
    const res = await fetch(`${BASE}/admin/capability-cache/evict?${q.toString()}`, { method: "POST" });
    if (!res.ok) throw new Error(`API error ${res.status}: ${await res.text()}`);
    return res.json() as Promise<{ status: string; scope: string; evicted?: number }>;
  },
};
