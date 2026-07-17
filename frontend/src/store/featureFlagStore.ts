import { create } from "zustand";
import type { FeatureFlagCard, PlanCaptureStatus, FederatedSyncStatus } from "../api/controlPanel";

interface FeatureFlagState {
  flags: FeatureFlagCard[];
  loading: boolean;
  error: string | null;
  planCaptureStatus: PlanCaptureStatus | null;
  federatedSyncStatus: FederatedSyncStatus | null;

  setFlags: (flags: FeatureFlagCard[]) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  updateFlagEnabled: (flagId: string, enabled: boolean) => void;
  setPlanCaptureStatus: (status: PlanCaptureStatus | null) => void;
  setFederatedSyncStatus: (status: FederatedSyncStatus | null) => void;
}

export const useFeatureFlagStore = create<FeatureFlagState>((set) => ({
  flags: [],
  loading: false,
  error: null,
  planCaptureStatus: null,
  federatedSyncStatus: null,

  setFlags: (flags) => set({ flags }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error, loading: false }),
  updateFlagEnabled: (flagId, enabled) =>
    set((state) => ({
      flags: state.flags.map((f) => (f.id === flagId ? { ...f, enabled } : f)),
    })),
  setPlanCaptureStatus: (status) => set({ planCaptureStatus: status }),
  setFederatedSyncStatus: (status) => set({ federatedSyncStatus: status }),
}));

/** Convenience selector for other modules (e.g. TransformPanel) to check
 * whether a specific capability is enabled without importing the whole
 * Control Panel UI. Defaults to false until the Control Panel has loaded
 * the flag list at least once. */
export function useFeatureFlagEnabled(flagId: string): boolean {
  return useFeatureFlagStore((s) => s.flags.find((f) => f.id === flagId)?.enabled ?? false);
}
