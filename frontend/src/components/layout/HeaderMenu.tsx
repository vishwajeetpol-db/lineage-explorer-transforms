import { memo, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Menu, Activity, FolderOpen, Home, SlidersHorizontal, BarChart3, BookOpen, Bell, Download, Search, Monitor, Radio } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { goLanding, goCatalogs, goControlPanel, goDQ, goGlossary, goNotifications, goExport, goRootCause, goBiConsumers, goStreaming } from "../../hooks/useRouter";

interface Props {
  variant?: "default" | "floating";
}

function HeaderMenu({ variant = "default" }: Props) {
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const [open, setOpen] = useState(false);

  const buttonClass =
    variant === "floating"
      ? "flex items-center justify-center w-9 h-9 rounded-xl bg-surface-50/80 hover:bg-surface-50 backdrop-blur-md border border-white/[0.08] hover:border-accent/30 transition-all duration-200"
      : "flex items-center justify-center w-8 h-8 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.06] hover:border-white/[0.12] transition-all duration-200";

  return (
    <div className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className={buttonClass}
        title="Menu"
        aria-label="Open menu"
      >
        <Menu size={variant === "floating" ? 16 : 15} className="text-slate-400" />
      </button>
      <AnimatePresence>
        {open && (
          <>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-[90]"
              onClick={() => setOpen(false)}
            />
            <motion.div
              initial={{ opacity: 0, y: -4, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.96 }}
              transition={{ duration: 0.15 }}
              className="absolute right-0 top-full mt-2 z-[100] w-56 rounded-xl bg-[#161625]/95 backdrop-blur-xl border border-white/[0.08] shadow-[0_12px_40px_rgba(0,0,0,0.5)] overflow-hidden max-h-[80vh] overflow-y-auto"
            >
              <button onClick={() => { setOpen(false); goLanding(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Home size={14} className="text-accent-light" />
                <span className="text-[12px] text-slate-300 font-medium">Home</span>
              </button>
              <button onClick={() => { setOpen(false); goCatalogs(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <FolderOpen size={14} className="text-indigo-400" />
                <span className="text-[12px] text-slate-300 font-medium">Browse catalogs</span>
              </button>
              <button onClick={() => { setOpen(false); goControlPanel(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <SlidersHorizontal size={14} className="text-violet-400" />
                <span className="text-[12px] text-slate-300 font-medium">Control Panel</span>
              </button>
              <button onClick={() => { setOpen(false); goDQ(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <BarChart3 size={14} className="text-cyan-400" />
                <span className="text-[12px] text-slate-300 font-medium">Data Quality</span>
              </button>
              <button onClick={() => { setOpen(false); goGlossary(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <BookOpen size={14} className="text-amber-400" />
                <span className="text-[12px] text-slate-300 font-medium">Business Glossary</span>
              </button>
              <button onClick={() => { setOpen(false); goNotifications(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Bell size={14} className="text-rose-400" />
                <span className="text-[12px] text-slate-300 font-medium">Notifications</span>
              </button>
              <button onClick={() => { setOpen(false); goExport(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Download size={14} className="text-teal-400" />
                <span className="text-[12px] text-slate-300 font-medium">OpenLineage Export</span>
              </button>
              <button onClick={() => { setOpen(false); goRootCause(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Search size={14} className="text-orange-400" />
                <span className="text-[12px] text-slate-300 font-medium">Root Cause Analysis</span>
              </button>
              <button onClick={() => { setOpen(false); goBiConsumers(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Monitor size={14} className="text-blue-400" />
                <span className="text-[12px] text-slate-300 font-medium">BI Consumers</span>
              </button>
              <button onClick={() => { setOpen(false); goStreaming(); }} className="w-full flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors border-b border-white/[0.04] text-left">
                <Radio size={14} className="text-emerald-400" />
                <span className="text-[12px] text-slate-300 font-medium">Streaming Topology</span>
              </button>
              {isAdmin && (
                <a
                  href="/?admin=true"
                  target="_blank"
                  rel="noopener noreferrer"
                  onClick={() => setOpen(false)}
                  className="flex items-center gap-2.5 px-4 py-3 hover:bg-white/[0.04] transition-colors"
                >
                  <Activity size={14} className="text-emerald-400" />
                  <span className="text-[12px] text-slate-300 font-medium">Admin Dashboard</span>
                </a>
              )}
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </div>
  );
}

export default memo(HeaderMenu);
