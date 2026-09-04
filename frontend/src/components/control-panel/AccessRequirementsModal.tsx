import { memo, useEffect, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { X, CheckCircle2, XCircle, HelpCircle, Loader2 } from "lucide-react";
import { checkAccessRequirements } from "../../api/controlPanel";
import type { FeatureFlagCard, AccessRequirement } from "../../api/controlPanel";

interface Props {
  flag: FeatureFlagCard | null;
  onClose: () => void;
}

function StatusIcon({ satisfied }: { satisfied: boolean | null | undefined }) {
  if (satisfied === true) return <CheckCircle2 size={14} className="text-emerald-400 flex-shrink-0 mt-0.5" />;
  if (satisfied === false) return <XCircle size={14} className="text-red-400 flex-shrink-0 mt-0.5" />;
  return <HelpCircle size={14} className="text-slate-500 flex-shrink-0 mt-0.5" />;
}

function AccessRequirementsModal({ flag, onClose }: Props) {
  const [checked, setChecked] = useState<AccessRequirement[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setChecked(null);
    setError(null);
  }, [flag?.id]);

  if (!flag) return null;

  const runCheck = async () => {
    setChecking(true);
    setError(null);
    try {
      const r = await checkAccessRequirements(flag.id);
      setChecked(r.requirements);
    } catch (e: any) {
      setError(e.message || "Access check failed");
    } finally {
      setChecking(false);
    }
  };

  const requirements: AccessRequirement[] = checked || flag.access_requirements;

  return (
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[9998]"
        onClick={onClose}
      />
      <motion.div
        initial={{ opacity: 0, scale: 0.96, y: 12 }}
        animate={{ opacity: 1, scale: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.96, y: 12 }}
        transition={{ duration: 0.18 }}
        className="fixed inset-0 z-[9999] flex items-center justify-center p-4 pointer-events-none"
      >
        <div className="pointer-events-auto w-full max-w-[560px] max-h-[80vh] overflow-y-auto rounded-2xl bg-[#14141F]/98 border border-white/[0.08] shadow-[0_20px_60px_rgba(0,0,0,0.5)] backdrop-blur-xl">
          <div className="sticky top-0 flex items-center justify-between px-5 py-4 border-b border-white/[0.06] bg-[#14141F]/98">
            <div>
              <div className="text-[13px] font-semibold text-white">{flag.name}</div>
              <div className="text-[11px] text-slate-500 mt-0.5">Access requirements</div>
            </div>
            <button onClick={onClose} className="text-slate-500 hover:text-slate-300 transition-colors" aria-label="Close">
              <X size={18} />
            </button>
          </div>
          <div className="p-5 space-y-3">
            {requirements.map((req, i) => (
              <div key={i} className="flex items-start gap-2.5 p-3 rounded-lg bg-white/[0.02] border border-white/[0.05]">
                <StatusIcon satisfied={req.satisfied} />
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-medium text-slate-200">{req.privilege}</div>
                  <div className="text-[11px] font-mono text-slate-500 mt-0.5 truncate" title={req.scope}>{req.scope}</div>
                  <div className="text-[11px] text-slate-400 mt-1">{req.reason}</div>
                  {req.detail && <div className="text-[10px] text-slate-500 mt-1 italic">{req.detail}</div>}
                </div>
              </div>
            ))}
            {error && <div className="text-[11px] text-red-400">{error}</div>}
            <button
              onClick={runCheck}
              disabled={checking}
              className="w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-white/[0.04] hover:bg-white/[0.07] border border-white/[0.08] text-[12px] text-slate-300 font-medium transition-colors disabled:opacity-50"
            >
              {checking && <Loader2 size={13} className="animate-spin" />}
              {checking ? "Checking access…" : "Run live access check"}
            </button>
          </div>
        </div>
      </motion.div>
    </AnimatePresence>
  );
}

export default memo(AccessRequirementsModal);
