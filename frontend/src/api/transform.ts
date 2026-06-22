/**
 * Transform API client — communicates with the backend transformation lineage
 * endpoints for the "microscopic" column-level drill-down.
 */

export interface TransformNode {
  node_id: string;
  table_fqn: string;
  column: string;
}

export interface TransformEdge {
  source_node_id: string;
  target_node_id: string;
  expression: string;
  category: string;
  category_color: string;
  source_file: string;
}

export interface TransformLevel {
  depth: number;
  label: string;
  color: string;
  nodes: TransformNode[];
  transforms: TransformEdge[];
}

export interface TransformResponse {
  levels: TransformLevel[];
  has_lineage: boolean;
  is_source_column: boolean;
  cached: boolean;
  cached_at: string | null;
  fetch_duration_ms: number | null;
  total_nodes: number;
  total_edges: number;
  max_depth_reached: number;
}

export interface FreshnessInfo {
  exists: boolean;
  edge_count: number;
  last_built: string | null;
  age_str: string;
  is_stale: boolean;
}

export interface BuildJobStatus {
  run_id: string;
  state: string;
  result_state: string | null;
  state_message: string;
  progress_pct: number;
  is_complete: boolean;
  is_success: boolean;
  current_step: number;
  current_step_name: string;
  total_steps: number;
  steps: string[];
  run_page_url: string;
}

export interface BuildSubmitResponse {
  status: 'submitted' | 'skipped' | 'fresh';
  run_id?: string;
  message?: string;
  reason?: string;
  table_fqn?: string;
  freshness?: FreshnessInfo;
}

export interface TransformCategories {
  categories: Record<string, string>;
  level_colors: string[];
}

export interface BuildConfig {
  configured: boolean;
}

const BASE = '/api/transform';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/** Check if transformation lineage exists and is fresh for a table. */
export async function getTransformFreshness(
  catalog: string,
  schema: string,
  table: string,
): Promise<FreshnessInfo> {
  const params = new URLSearchParams({ catalog, schema, table });
  return fetchJson<FreshnessInfo>(`${BASE}/freshness?${params}`);
}

/** Backtrack upstream transformation lineage for a specific column. */
export async function getTransformTrace(
  catalog: string,
  schema: string,
  table: string,
  column: string,
  maxDepth = 8,
): Promise<TransformResponse> {
  const params = new URLSearchParams({
    catalog,
    schema,
    table,
    column,
    max_depth: String(maxDepth),
  });
  return fetchJson<TransformResponse>(`${BASE}/trace?${params}`);
}

/** Submit a build job for transformation lineage. */
export async function submitTransformBuild(
  tableFqn: string,
  forceRebuild = false,
): Promise<BuildSubmitResponse> {
  return fetchJson<BuildSubmitResponse>(`${BASE}/build`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ table_fqn: tableFqn, force_rebuild: forceRebuild }),
  });
}

/** Poll the status of a build job. */
export async function getBuildStatus(runId: string): Promise<BuildJobStatus> {
  return fetchJson<BuildJobStatus>(`${BASE}/status/${runId}`);
}

/** Get transformation category → color mapping. */
export async function getTransformCategories(): Promise<TransformCategories> {
  return fetchJson<TransformCategories>(`${BASE}/categories`);
}

/** Check if build pipeline is configured. */
export async function getBuildConfig(): Promise<BuildConfig> {
  return fetchJson<BuildConfig>(`${BASE}/build-configured`);
}
