import { memo, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { GitBranch, Search, FolderOpen, ChevronRight, Loader2, RefreshCw, Database, Eye, Layers, Zap, HardDrive, Clock, FolderTree } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { api } from "../../api/client";
import { useRecents } from "../../hooks/useRecents";
import { goCatalogs } from "../../hooks/useRouter";
import HeaderMenu from "../layout/HeaderMenu";
import LineagePicker from "./LineagePicker";
import type { TableSearchItem } from "../../api/client";

const typeIcons: Record<string, typeof Database> = {
  MANAGED: Database,
  TABLE: Database,
  EXTERNAL: Database,
  VIEW: Eye,
  MATERIALIZED_VIEW: Layers,
  STREAMING_TABLE: Zap,
  VOLUME: FolderOpen,
  PATH: HardDrive,
};

const typeColors: Record<string, string> = {
  MANAGED: "text-blue-400",
  TABLE: "text-blue-400",
  EXTERNAL: "text-emerald-400",
  VIEW: "text-emerald-400",
  MATERIALIZED_VIEW: "text-amber-400",
  STREAMING_TABLE: "text-rose-400",
  VOLUME: "text-violet-400",
  PATH: "text-orange-400",
};

interface Props {
  onSelectTable: (fqdn: string) => void;
}

function Landing({ onSelectTable }: Props) {
  const allTables = useLineageStore((s) => s.allTables);
  const allTablesLoading = useLineageStore((s) => s.allTablesLoading);
  const setGlobalSearchOpen = useLineageStore((s) => s.setGlobalSearchOpen);
  const { recents } = useRecents();
  const [pickerMode, setPickerMode] = useState<"schema" | "catalog" | null>(null);

  const catalogCount = useMemo(() => {
    const set = new Set<string>();
    for (const t of allTables) set.add(t.catalog);
    return set.size;
  }, [allTables]);

  const recentItems = useMemo(() => {
    const byFqdn = new Map(allTables.map((t) => [t.fqdn, t]));
    return recents
      .map((fqdn) => byFqdn.get(fqdn))
      .filter((t): t is TableSearchItem => Boolean(t))
      .slice(0, 5);
  }, [recents, allTables]);

  // Loading state
  if (allTablesLoading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-surface">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(99,102,241,0.06)_0%,transparent_70%)]" />
        <div className="relative z-10 flex flex-col items-center gap-4">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-accent to-purple-500 flex items-center justify-center shadow-[0_0_40px_rgba(99,102,241,0.25)]">
            <GitBranch size={26} className="text-white" />
          </div>
          <Loader2 size={24} className="text-accent animate-spin" />
          <p className="text-sm text-slate-500">Loading tables...</p>
        </div>
      </div>
    );
  }

  // Empty / error state
  if (allTables.length === 0) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-surface">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(99,102,241,0.06)_0%,transparent_70%)]" />
        <div className="relative z-10 flex flex-col items-center gap-4">
          <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-accent to-purple-500 flex items-center justify-center shadow-[0_0_40px_rgba(99,102,241,0.25)]">
            <GitBranch size={26} className="text-white" />
          </div>
          <p className="text-sm text-slate-500">Unable to load table index</p>
          <button
            onClick={() => {
              useLineageStore.getState().setAllTablesLoading(true);
              api.getTables()
                .then((r) => useLineageStore.getState().setAllTables(r.tables))
                .catch(() => useLineageStore.getState().setAllTablesLoading(false));
            }}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 hover:bg-accent/20 border border-accent/20 text-accent-light text-[12px] font-medium transition-all duration-200"
          >
            <RefreshCw size={13} />
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen w-screen flex flex-col items-center bg-surface overflow-hidden relative">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(99,102,241,0.06)_0%,transparent_60%)]" />

      {/* Floating menu in top-right */}
      <div className="absolute top-5 right-5 z-20">
        <HeaderMenu variant="floating" />
      </div>

      <div className="relative z-10 flex flex-col items-center gap-16 w-full max-w-2xl px-8 mt-[10vh]">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="flex items-center gap-4"
        >
          <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-accent to-purple-500 flex items-center justify-center shadow-[0_0_40px_rgba(99,102,241,0.3)]">
            <GitBranch size={22} className="text-white" />
          </div>
          <div>
            <h1 className="text-[22px] font-semibold text-white tracking-tight">Lineage Explorer</h1>
            <p className="text-[12px] text-slate-500">Click any table to explore its data lineage</p>
          </div>
        </motion.div>

        {/* Tile row: Browse · Schema lineage · Catalog lineage */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 w-full">
          {/* Browse catalog */}
          <motion.button
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.08 }}
            whileHover={{ y: -3, scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={goCatalogs}
            className="group flex flex-col items-center gap-3 px-6 py-7 bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.08] hover:border-accent/40 rounded-3xl transition-all duration-200 shadow-[0_8px_30px_rgba(0,0,0,0.3)]"
          >
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-indigo-500/25 to-purple-500/25 flex items-center justify-center group-hover:from-indigo-500/35 group-hover:to-purple-500/35 transition-all">
              <FolderOpen size={22} className="text-indigo-400" />
            </div>
            <div className="text-center">
              <div className="text-[15px] font-semibold text-slate-100">Browse</div>
              <div className="text-[11px] text-slate-500 mt-0.5">
                {catalogCount} catalog{catalogCount !== 1 && "s"} · {allTables.length.toLocaleString()} tables
              </div>
            </div>
            <div className="flex items-center gap-1 text-[11px] text-slate-500 group-hover:text-accent-light transition-colors mt-1">
              Explore <ChevronRight size={11} />
            </div>
          </motion.button>

          {/* Schema lineage */}
          <motion.button
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.12 }}
            whileHover={{ y: -3, scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => setPickerMode("schema")}
            className="group flex flex-col items-center gap-3 px-6 py-7 bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.08] hover:border-accent/40 rounded-3xl transition-all duration-200 shadow-[0_8px_30px_rgba(0,0,0,0.3)]"
          >
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-sky-500/25 to-cyan-500/25 flex items-center justify-center group-hover:from-sky-500/35 group-hover:to-cyan-500/35 transition-all">
              <Layers size={22} className="text-sky-400" />
            </div>
            <div className="text-center">
              <div className="text-[15px] font-semibold text-slate-100">Schema lineage</div>
              <div className="text-[11px] text-slate-500 mt-0.5">Map every table in a schema</div>
            </div>
            <div className="flex items-center gap-1 text-[11px] text-slate-500 group-hover:text-accent-light transition-colors mt-1">
              Choose schema <ChevronRight size={11} />
            </div>
          </motion.button>

          {/* Catalog lineage */}
          <motion.button
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            transition={{ delay: 0.16 }}
            whileHover={{ y: -3, scale: 1.02 }}
            whileTap={{ scale: 0.98 }}
            onClick={() => setPickerMode("catalog")}
            className="group flex flex-col items-center gap-3 px-6 py-7 bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.08] hover:border-accent/40 rounded-3xl transition-all duration-200 shadow-[0_8px_30px_rgba(0,0,0,0.3)]"
          >
            <div className="w-12 h-12 rounded-2xl bg-gradient-to-br from-violet-500/25 to-fuchsia-500/25 flex items-center justify-center group-hover:from-violet-500/35 group-hover:to-fuchsia-500/35 transition-all">
              <FolderTree size={22} className="text-violet-400" />
            </div>
            <div className="text-center">
              <div className="text-[15px] font-semibold text-slate-100">Catalog lineage</div>
              <div className="text-[11px] text-slate-500 mt-0.5">Map every table in a catalog</div>
            </div>
            <div className="flex items-center gap-1 text-[11px] text-slate-500 group-hover:text-accent-light transition-colors mt-1">
              Choose catalog <ChevronRight size={11} />
            </div>
          </motion.button>
        </div>

        {/* Global search bar */}
        <motion.button
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15 }}
          onClick={() => setGlobalSearchOpen(true)}
          className="group w-full flex items-center gap-3 px-5 py-4 bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.08] hover:border-accent/40 rounded-2xl transition-all duration-200"
        >
          <Search size={16} className="text-slate-500 group-hover:text-accent-light transition-colors" />
          <span className="font-mono text-[13px] text-slate-500 group-hover:text-slate-300 transition-colors flex-1 text-left">
            Search any table across all catalogs and schemas
          </span>
          <kbd className="text-[10px] text-slate-600 bg-surface-200 px-1.5 py-0.5 rounded font-mono">⌘K</kbd>
        </motion.button>

        {/* Recently viewed */}
        {recentItems.length > 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ delay: 0.22 }}
            className="w-full"
          >
            <div className="flex items-center gap-2 mb-3 text-[10px] uppercase tracking-wider text-slate-600 font-medium">
              <Clock size={11} />
              Recently viewed
            </div>
            <div className="space-y-1 border border-white/[0.06] rounded-xl overflow-hidden bg-surface-50/40">
              {recentItems.map((t) => {
                const Icon = typeIcons[t.table_type] || Database;
                const color = typeColors[t.table_type] || "text-blue-400";
                return (
                  <button
                    key={t.fqdn}
                    onClick={() => onSelectTable(t.fqdn)}
                    className="group w-full flex items-center gap-3 px-4 py-2.5 hover:bg-accent/[0.06] transition-colors border-b border-white/[0.04] last:border-b-0"
                  >
                    <Icon size={13} className={color} />
                    <span className="font-mono text-[12px] text-slate-300 group-hover:text-accent-light transition-colors flex-1 truncate text-left">
                      {t.fqdn}
                    </span>
                    <ChevronRight size={12} className="text-slate-600 group-hover:text-accent-light transition-colors" />
                  </button>
                );
              })}
            </div>
          </motion.div>
        )}
      </div>

      {pickerMode && <LineagePicker mode={pickerMode} onClose={() => setPickerMode(null)} />}
    </div>
  );
}

export default memo(Landing);
