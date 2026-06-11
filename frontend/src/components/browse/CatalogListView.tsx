import { memo, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Database, Search, FolderOpen, ChevronRight, GitBranch } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { goSchemas, goCatalogLineage } from "../../hooks/useRouter";
import Breadcrumb from "./Breadcrumb";
import PageShell from "./PageShell";

function CatalogListView() {
  const allTables = useLineageStore((s) => s.allTables);
  const [filter, setFilter] = useState("");

  // Aggregate per-catalog stats
  const catalogStats = useMemo(() => {
    const byCatalog = new Map<string, { schemas: Set<string>; tables: number }>();
    for (const t of allTables) {
      const entry = byCatalog.get(t.catalog) ?? { schemas: new Set(), tables: 0 };
      entry.schemas.add(t.schema);
      entry.tables += 1;
      byCatalog.set(t.catalog, entry);
    }
    return Array.from(byCatalog.entries())
      .map(([catalog, { schemas, tables }]) => ({
        catalog,
        schemaCount: schemas.size,
        tableCount: tables,
      }))
      .sort((a, b) => a.catalog.localeCompare(b.catalog));
  }, [allTables]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return catalogStats;
    const q = filter.toLowerCase();
    return catalogStats.filter((c) => c.catalog.toLowerCase().includes(q));
  }, [catalogStats, filter]);

  return (
    <PageShell>
      <div className="flex items-center justify-between mb-6">
        <div className="space-y-2">
          <Breadcrumb />
          <h2 className="text-[20px] font-semibold text-white tracking-tight">
            Catalogs <span className="text-slate-500 font-normal text-[14px] ml-1">({catalogStats.length})</span>
          </h2>
        </div>

        <div className="relative w-72">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter catalogs..."
            className="w-full pl-9 pr-3 py-2 bg-surface-50/80 border border-white/[0.06] rounded-lg text-[13px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-accent/40 transition-colors font-mono"
          />
        </div>
      </div>

      {filtered.length === 0 ? (
        <div className="flex items-center justify-center py-16">
          <p className="text-sm text-slate-500">No catalogs matching &ldquo;{filter}&rdquo;</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((c, i) => (
            <motion.div
              key={c.catalog}
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: Math.min(i * 0.02, 0.15) }}
              whileHover={{ y: -2 }}
              className="group flex flex-col bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.06] hover:border-accent/30 rounded-2xl p-5 transition-all duration-200"
            >
              <button onClick={() => goSchemas(c.catalog)} className="text-left" title="Browse schemas">
                <div className="flex items-start justify-between mb-3">
                  <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-indigo-500/20 to-purple-500/20 flex items-center justify-center">
                    <FolderOpen size={18} className="text-indigo-400" />
                  </div>
                  <ChevronRight size={14} className="text-slate-600 group-hover:text-accent-light transition-colors mt-2" />
                </div>
                <div className="font-mono text-[14px] text-slate-200 font-medium mb-2 truncate">
                  {c.catalog}
                </div>
                <div className="flex items-center gap-3 text-[11px] text-slate-500">
                  <span className="flex items-center gap-1">
                    <Database size={11} />
                    {c.schemaCount} schema{c.schemaCount !== 1 && "s"}
                  </span>
                  <span className="text-slate-700">·</span>
                  <span>{c.tableCount.toLocaleString()} table{c.tableCount !== 1 && "s"}</span>
                </div>
              </button>
              <button
                onClick={() => goCatalogLineage(c.catalog)}
                className="mt-4 flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-white/[0.03] hover:bg-accent/15 border border-white/[0.06] hover:border-accent/30 text-slate-400 hover:text-accent-light text-[11px] font-medium transition-all duration-200"
                title="Visualize lineage for every table in this catalog"
              >
                <GitBranch size={12} />
                View full lineage
              </button>
            </motion.div>
          ))}
        </div>
      )}
    </PageShell>
  );
}

export default memo(CatalogListView);
