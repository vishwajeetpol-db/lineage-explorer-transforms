import { memo, ReactNode } from "react";
import { motion } from "framer-motion";
import { GitBranch, Search } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { goLanding } from "../../hooks/useRouter";
import HeaderMenu from "../layout/HeaderMenu";

interface Props {
  children: ReactNode;
}

function PageShell({ children }: Props) {
  const setGlobalSearchOpen = useLineageStore((s) => s.setGlobalSearchOpen);

  return (
    <div className="h-screen w-screen flex flex-col bg-surface overflow-hidden">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(99,102,241,0.04)_0%,transparent_50%)]" />

      {/* Header */}
      <motion.div
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        className="relative z-10 flex items-center gap-4 px-8 py-5 border-b border-white/[0.06]"
      >
        <button
          onClick={goLanding}
          className="flex items-center gap-3 hover:opacity-90 transition-opacity"
        >
          <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-accent to-purple-500 flex items-center justify-center shadow-[0_0_30px_rgba(99,102,241,0.2)]">
            <GitBranch size={20} className="text-white" />
          </div>
          <div className="text-left">
            <h1 className="text-lg font-semibold text-white tracking-tight">BrickRoute</h1>
            <p className="text-[11px] text-slate-500">Click any table to explore its data lineage</p>
          </div>
        </button>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => setGlobalSearchOpen(true)}
            className="flex items-center gap-2 px-3 py-2 bg-surface-50/80 hover:bg-surface-50 border border-white/[0.06] hover:border-accent/30 rounded-lg text-[12px] text-slate-400 hover:text-slate-200 transition-all duration-200 w-72"
          >
            <Search size={13} />
            <span className="font-mono">Search any table...</span>
            <kbd className="ml-auto text-[10px] text-slate-600 bg-surface-200 px-1.5 py-0.5 rounded font-mono">⌘K</kbd>
          </button>
          <HeaderMenu />
        </div>
      </motion.div>

      {/* Content area */}
      <div className="relative z-10 flex-1 overflow-y-auto scrollbar-thin">
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.05 }}
          className="max-w-6xl mx-auto px-8 py-8"
        >
          {children}
        </motion.div>
      </div>
    </div>
  );
}

export default memo(PageShell);
