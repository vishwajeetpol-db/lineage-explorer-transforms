/**
 * Transform Store — Zustand state for the transformation lineage drill-down.
 *
 * Manages:
 * - Freshness status for the selected table
 * - Build job lifecycle (submit → poll → complete)
 * - Backtrack trace results (levels, nodes, edges)
 * - UI state (panel open/closed, selected column, loading states)
 * - Pruning controls (depth slider, category filter, path isolation)
 */

import { create } from 'zustand';
import {
  type TransformResponse,
  type FreshnessInfo,
  type BuildJobStatus,
  getTransformFreshness,
  getTransformTrace,
  submitTransformBuild,
  getBuildStatus,
  getTransformCategories,
} from '../api/transform';

export type TransformPanelState =
  | 'closed'
  | 'loading'
  | 'needs_build' // lineage missing/stale — await explicit, cost-incurring user trigger
  | 'building'
  | 'ready'
  | 'error';

interface TransformState {
  // Panel visibility
  panelState: TransformPanelState;
  panelError: string | null;

  // Selected target for transform trace
  selectedTable: string | null; // catalog.schema.table
  selectedColumn: string | null;

  // Freshness
  freshness: FreshnessInfo | null;
  freshnessLoading: boolean;

  // Build job
  buildRunId: string | null;
  buildStatus: BuildJobStatus | null;
  buildPolling: boolean;

  // Transform trace result
  traceResult: TransformResponse | null;
  traceLoading: boolean;

  // Categories (for legend)
  categories: Record<string, string>;

  // ---------------------------------------------------------------------------
  // Pruning controls — client-side graph filtering
  // ---------------------------------------------------------------------------

  /** Max depth to fetch from backend (re-fetches on change). 1–8. */
  maxDepth: number;

  /** Categories hidden from the graph (client-side filter, no re-fetch). */
  hiddenCategories: Set<string>;

  /** When set, only the path between this node and the target is visible. */
  isolatedNodeId: string | null;

  // Actions
  openPanel: (tableFqn: string, column: string) => Promise<void>;
  closePanel: () => void;
  checkFreshness: (catalog: string, schema: string, table: string) => Promise<FreshnessInfo>;
  triggerBuild: (tableFqn: string, forceRebuild?: boolean) => Promise<void>;
  pollBuild: () => Promise<void>;
  loadTrace: (catalog: string, schema: string, table: string, column: string, depth?: number) => Promise<void>;
  loadCategories: () => Promise<void>;
  reset: () => void;

  // Pruning actions
  setMaxDepth: (depth: number) => void;
  toggleCategory: (category: string) => void;
  showAllCategories: () => void;
  hideAllCategories: () => void;
  isolateNode: (nodeId: string | null) => void;
  clearIsolation: () => void;
}

const INITIAL_STATE = {
  panelState: 'closed' as TransformPanelState,
  panelError: null,
  selectedTable: null,
  selectedColumn: null,
  freshness: null,
  freshnessLoading: false,
  buildRunId: null,
  buildStatus: null,
  buildPolling: false,
  traceResult: null,
  traceLoading: false,
  categories: {} as Record<string, string>,
  maxDepth: 8,
  hiddenCategories: new Set<string>(),
  isolatedNodeId: null,
};

export const useTransformStore = create<TransformState>((set, get) => ({
  ...INITIAL_STATE,

  openPanel: async (tableFqn: string, column: string) => {
    const [catalog, schema, table] = tableFqn.split('.');
    if (!catalog || !schema || !table) {
      set({ panelState: 'error', panelError: 'Invalid table name' });
      return;
    }

    // Stop any existing poll from a previous open before resetting state
    set({
      panelState: 'loading',
      panelError: null,
      selectedTable: tableFqn,
      selectedColumn: column,
      traceResult: null,
      buildPolling: false,
      buildRunId: null,
      buildStatus: null,
      // Reset pruning on new column selection
      maxDepth: 8,
      hiddenCategories: new Set<string>(),
      isolatedNodeId: null,
    });

    try {
      // 1. Check freshness
      const freshness = await getTransformFreshness(catalog, schema, table);

      // Guard: if user clicked a different column while we were awaiting, abort
      if (get().selectedTable !== tableFqn || get().selectedColumn !== column) {
        return;
      }

      set({ freshness });

      if (!freshness.exists || freshness.is_stale) {
        // Do NOT auto-build. Transformation lineage is an explicit, opt-in,
        // compute-cost action. The column (UC) lineage is already shown on the
        // main graph; here we just surface a prompt + cost note and let the
        // user decide to generate. The build only runs on an explicit click.
        set({ panelState: 'needs_build' });
        return;
      }

      // 2. Lineage exists and is fresh — load trace (read-only, no build)
      await get().loadTrace(catalog, schema, table, column);
    } catch (err: any) {
      // Only set error if this is still the active panel request
      if (get().selectedTable === tableFqn && get().selectedColumn === column) {
        set({ panelState: 'error', panelError: err.message || 'Failed to load' });
      }
    }
  },

  closePanel: () => {
    set(INITIAL_STATE);
  },

  checkFreshness: async (catalog: string, schema: string, table: string) => {
    set({ freshnessLoading: true });
    try {
      const freshness = await getTransformFreshness(catalog, schema, table);
      set({ freshness, freshnessLoading: false });
      return freshness;
    } catch (err: any) {
      set({ freshnessLoading: false });
      throw err;
    }
  },

  triggerBuild: async (tableFqn: string, forceRebuild = false) => {
    set({ panelState: 'building', buildStatus: null });
    try {
      const resp = await submitTransformBuild(tableFqn, forceRebuild);
      if (resp.status === 'submitted' && resp.run_id) {
        set({ buildRunId: resp.run_id, buildPolling: true });
        // Start polling
        get().pollBuild();
      } else if (resp.status === 'fresh' || resp.status === 'skipped') {
        // Already fresh or skipped — load trace directly
        const [catalog, schema, table] = tableFqn.split('.');
        await get().loadTrace(catalog, schema, table, get().selectedColumn || '');
      } else {
        // Unknown status — treat as error
        set({ panelState: 'error', panelError: resp.message || 'Unexpected build response' });
      }
    } catch (err: any) {
      set({ panelState: 'error', panelError: err.message || 'Build failed' });
    }
  },

  pollBuild: async () => {
    const { buildRunId } = get();
    if (!buildRunId) return;

    const poll = async () => {
      // Guard: if panel was closed or polling was stopped, abort immediately.
      // This prevents the orphaned-timeout race condition where closePanel()
      // resets state but a previously-scheduled setTimeout still fires.
      if (!get().buildPolling || get().panelState === 'closed') {
        return;
      }

      try {
        const status = await getBuildStatus(buildRunId);

        // Re-check after await — panel may have been closed during the fetch
        if (!get().buildPolling || get().panelState === 'closed') {
          return;
        }

        set({ buildStatus: status });

        if (status.is_complete) {
          set({ buildPolling: false });
          if (status.is_success) {
            // Build done — load the trace
            const tableFqn = get().selectedTable;
            const column = get().selectedColumn;
            if (tableFqn && column) {
              const [catalog, schema, table] = tableFqn.split('.');
              await get().loadTrace(catalog, schema, table, column);
            }
          } else {
            set({
              panelState: 'error',
              panelError: `Build failed: ${status.state_message || status.result_state || 'Unknown error'}`,
            });
          }
          return;
        }

        // Continue polling — re-check guard before scheduling next tick
        if (get().buildPolling && get().panelState !== 'closed') {
          setTimeout(poll, 3000);
        }
      } catch (err: any) {
        // Only set error if panel is still open
        if (get().panelState !== 'closed') {
          set({
            panelState: 'error',
            panelError: `Polling error: ${err.message}`,
            buildPolling: false,
          });
        }
      }
    };

    poll();
  },

  loadTrace: async (catalog: string, schema: string, table: string, column: string, depth?: number) => {
    const maxDepth = depth ?? get().maxDepth;
    set({ traceLoading: true, panelState: 'loading' });
    try {
      const result = await getTransformTrace(catalog, schema, table, column, maxDepth);
      set({
        traceResult: result,
        traceLoading: false,
        panelState: 'ready',
      });
    } catch (err: any) {
      set({
        traceLoading: false,
        panelState: 'error',
        panelError: err.message || 'Failed to load transformation trace',
      });
    }
  },

  loadCategories: async () => {
    try {
      const data = await getTransformCategories();
      set({ categories: data.categories });
    } catch {
      // Non-critical — categories are just for the legend
    }
  },

  reset: () => set(INITIAL_STATE),

  // ---------------------------------------------------------------------------
  // Pruning actions
  // ---------------------------------------------------------------------------

  setMaxDepth: (depth: number) => {
    const clamped = Math.max(1, Math.min(8, depth));
    set({ maxDepth: clamped, isolatedNodeId: null });

    // Re-fetch trace with new depth
    const { selectedTable, selectedColumn } = get();
    if (selectedTable && selectedColumn) {
      const [catalog, schema, table] = selectedTable.split('.');
      if (catalog && schema && table) {
        get().loadTrace(catalog, schema, table, selectedColumn, clamped);
      }
    }
  },

  toggleCategory: (category: string) => {
    const current = get().hiddenCategories;
    const next = new Set(current);
    if (next.has(category)) {
      next.delete(category);
    } else {
      next.add(category);
    }
    set({ hiddenCategories: next });
  },

  showAllCategories: () => {
    set({ hiddenCategories: new Set<string>() });
  },

  hideAllCategories: () => {
    // Gather all categories from the current trace result
    const { traceResult } = get();
    if (!traceResult) return;
    const allCats = new Set<string>();
    for (const level of traceResult.levels) {
      for (const t of level.transforms) {
        allCats.add(t.category);
      }
    }
    set({ hiddenCategories: allCats });
  },

  isolateNode: (nodeId: string | null) => {
    set({ isolatedNodeId: nodeId });
  },

  clearIsolation: () => {
    set({ isolatedNodeId: null });
  },
}));
