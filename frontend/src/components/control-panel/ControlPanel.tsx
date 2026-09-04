import { memo, useEffect, useState, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, SlidersHorizontal, RefreshCw, Database, Share2 } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { useFeatureFlagStore } from "../../store/featureFlagStore";
import {
  getFeatureFlags,
  setFeatureFlag,
  getPlanCaptureStatus,
  getFederatedSyncStatus,
} from "../../api/controlPanel";
import type { FeatureFlagCard as FlagCardType } from "../../api/controlPanel";
import ModuleSection from "./ModuleSection";
import AccessRequirementsModal from "./AccessRequirementsModal";

interface Props {
  open: boolean;
  onClose: () => void;
}

function ControlPanel({ open, onClose }: Props) {
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const {
    flags, loading, error, planCaptureStatus, federatedSyncStatus,
    setFlags, setLoading, setError, updateFlagEnabled, setPlanCaptureStatus, setFederatedSyncStatus,
  } = useFeatureFlagStore();
  const [busyFlagId, setBusyFlagId] = useState<string | null>(null);
  const [accessModalFlag, setAccessModalFlag] = useState<FlagCardType | null>(null);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    getFeatureFlags()
      .then((r) => setFlags(r.flags))
      .catch((e: any) => setError(e.message || "Failed to load feature flags"));
    getPlanCaptureStatus().then(setPlanCaptureStatus).catch(() => {});
    getFederatedSyncStatus().then(setFederatedSyncStatus).catch(() => {});
  }, [setFlags, setLoading, setError, setPlanCaptureStatus, setFederatedSyncStatus]);

  useEffect(() => {
    if (open) refresh();
  }, [open, refresh]);

  const handleToggle = useCallback(
    async (flagId: string, next: boolean) => {
      setBusyFlagId(flagId);
      updateFlagEnabled(flagId, next); // optimistic
      try {
        await setFeatureFlag(flagId, next);
        // Status cards depend on flag state — refresh after a confirmed change.
        getPlanCaptureStatus().then(setPlanCaptureStatus).catch(() => {});
        getFederatedSyncStatus().then(setFederatedSyncStatus).catch(() => {});
      } catch (e: any) {
        updateFlagEnabled(flagId, !next); // revert on failure
        setError(e.message || "Failed to update flag");
      } finally {
        setBusyFlagId(null);
      }
    },
    [updateFlagEnabled, setError, setPlanCaptureStatus, setFederatedSyncStatus],
  );

  const byModule = (label: string) => flags.filter((f) => f.module_label === label);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[9996]"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.96, y: 16 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.96, y: 16 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-4 z-[9997] flex items-start justify-center pt-8 pointer-events-none"
          >
            <div className="pointer-events-auto w-full max-w-[720px] max-h-[85vh] overflow-y-auto rounded-2xl bg-[#0f0a17]/95 border border-violet-500/20 shadow-[0_0_60px_rgba(139,92,246,0.12)] backdrop-blur-xl">
              <div className="sticky top-0 z-10 flex items-center justify-between px-6 py-4 border-b border-violet-500/10 bg-[#0f0a17]/95 backdrop-blur-xl">
                <div className="flex items-center gap-3">
                  <SlidersHorizontal size={16} className="text-violet-400" />
                  <span className="font-semibold text-[14px] text-violet-100 tracking-tight">Control Panel</span>
                  {!isAdmin && (
                    <span className="text-[10px] px-2 py-0.5 rounded-full bg-white/[0.04] text-slate-500 border border-white/[0.06]">
                      Read-only — admin required to toggle
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  <button
                    onClick={refresh}
                    disabled={loading}
                    className="text-violet-400/70 hover:text-violet-300 transition-colors disabled:opacity-40"
                    title="Refresh"
                  >
                    <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
                  </button>
                  <button onClick={onClose} className="text-violet-400/50 hover:text-violet-300 transition-colors" aria-label="Close">
                    <X size={18} />
                  </button>
                </div>
              </div>

              {error && (
                <div className="px-6 py-3 text-[12px] text-red-400 bg-red-500/5 border-b border-red-500/10">{error}</div>
              )}

              <div className="p-6 space-y-6">
                <ModuleSection
                  moduleLabel="Lineage Tracking"
                  flags={byModule("Lineage Tracking")}
                  isAdmin={isAdmin}
                  busyFlagId={busyFlagId}
                  onToggle={handleToggle}
                  onOpenAccessCheck={setAccessModalFlag}
                  statusSlot={
                    planCaptureStatus ? (
                      <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                        <Database size={10} />
                        {planCaptureStatus.captured_plan_count.toLocaleString()} plans · {planCaptureStatus.distinct_targets} tables
                      </div>
                    ) : null
                  }
                />
                <ModuleSection
                  moduleLabel="Column Transformation"
                  flags={byModule("Column Transformation")}
                  isAdmin={isAdmin}
                  busyFlagId={busyFlagId}
                  onToggle={handleToggle}
                  onOpenAccessCheck={setAccessModalFlag}
                />
                <ModuleSection
                  moduleLabel="Federated Sync"
                  flags={byModule("Federated Sync")}
                  isAdmin={isAdmin}
                  busyFlagId={busyFlagId}
                  onToggle={handleToggle}
                  onOpenAccessCheck={setAccessModalFlag}
                  statusSlot={
                    federatedSyncStatus ? (
                      <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                        <Share2 size={10} />
                        {federatedSyncStatus.registered_peers} peers · {federatedSyncStatus.reachable_overlap} reachable
                      </div>
                    ) : null
                  }
                />
                {!loading && flags.length === 0 && !error && (
                  <div className="text-center py-8 text-[12px] text-slate-500">No capabilities registered.</div>
                )}
              </div>
            </div>
          </motion.div>
        </>
      )}
      <AccessRequirementsModal flag={accessModalFlag} onClose={() => setAccessModalFlag(null)} />
    </AnimatePresence>
  );
}

export default memo(ControlPanel);
