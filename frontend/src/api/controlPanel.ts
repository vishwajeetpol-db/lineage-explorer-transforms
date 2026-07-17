/**
 * Control Panel API client — feature flags, access-requirement checks, and
 * status cards for the Runtime Plan Capture and Federated Sync modules.
 * Mirrors the conventions in api/transform.ts.
 */

export interface AccessRequirement {
  privilege: string;
  scope: string;
  reason: string;
  satisfied: boolean | null; // null = could not be verified automatically
  detail?: string | null;
}

export interface FeatureFlagCard {
  id: string;
  module: string;
  module_label: string;
  accent: string;
  name: string;
  description: string;
  cost: "low" | "medium" | "high";
  risk: "low" | "medium" | "high";
  side_effects: string[];
  access_requirements: AccessRequirement[];
  depends_on: string[];
  enabled: boolean;
  kill_switched: boolean;
}

export interface PlanCaptureStatus {
  enabled: boolean;
  table_reachable: boolean;
  captured_plan_count: number;
  captured_cdc_spec_count: number;
  distinct_targets: number;
}

export interface FederatedPeer {
  peer_alias: string;
  share_name: string;
  direction: string;
  registered_by?: string | null;
  registered_at?: string | null;
  notes?: string | null;
}

export interface FederatedSyncStatus {
  enabled: boolean;
  registered_peers: number;
  known_shares: number;
  reachable_overlap: number;
}

const BASE = "/api/control-panel";

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(url, options);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ detail: resp.statusText }));
    throw new Error(body.detail || `HTTP ${resp.status}`);
  }
  return resp.json();
}

/** All Control Panel capabilities with live enabled state + static metadata. */
export async function getFeatureFlags(): Promise<{ flags: FeatureFlagCard[] }> {
  return fetchJson(`${BASE}/flags`);
}

/** Enable/disable a capability. Backend enforces admin-only — this will 403 for non-admins. */
export async function setFeatureFlag(
  flagId: string,
  enabled: boolean,
): Promise<{ status: string; flag_id: string; enabled: boolean }> {
  return fetchJson(`${BASE}/flags/${encodeURIComponent(flagId)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
}

/** Best-effort live check of a flag's access requirements. */
export async function checkAccessRequirements(
  flagId: string,
): Promise<{ flag_id: string; requirements: AccessRequirement[] }> {
  return fetchJson(`${BASE}/access-check/${encodeURIComponent(flagId)}`);
}

/** Runtime Plan Capture status card. */
export async function getPlanCaptureStatus(): Promise<PlanCaptureStatus> {
  return fetchJson(`${BASE}/plan-capture/status`);
}

/** Federated Sync status card. */
export async function getFederatedSyncStatus(): Promise<FederatedSyncStatus> {
  return fetchJson(`${BASE}/federated/status`);
}

export async function listFederatedPeers(): Promise<{ peers: FederatedPeer[] }> {
  return fetchJson(`${BASE}/federated/peers`);
}

/** Admin-only: register a known peer workspace/metastore. */
export async function registerFederatedPeer(
  peerAlias: string,
  shareName: string,
  direction: string,
  notes = "",
): Promise<{ status: string; peer_alias: string; share_name: string; direction: string }> {
  return fetchJson(`${BASE}/federated/peers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ peer_alias: peerAlias, share_name: shareName, direction, notes }),
  });
}
