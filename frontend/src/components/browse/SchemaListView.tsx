import { memo, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Search, Layers, ChevronRight, GitBranch, FolderTree } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { goTables, goSchemaLineage, goCatalogLineage } from "../../hooks/useRouter";
import Breadcrumb from "./Breadcrumb";
import PageShell from "./PageShell";

const MEDALLION_STYLES: Record<string, { badge: string; icon: string; emoji: string }> = {
  bronze: { badge: "bg-amber-700/20 text-amber-500 border-amber-700/30", icon: "text-amber-600", emoji: "🟫" },
  silver: { badge: "bg-slate-400/15 text-slate-300 border-slate-400/30", icon: "text-slate-300", emoji: "🪙" },
  gold: { badge: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30", icon: "text-yellow-400", emoji: "🥇" },
};

function detectMedallion(schema: string): "bronze" | "silver" | "gold" | null {
  const lower = schema.toLowerCase();
  if (lower === "bronze" || lower.startsWith("bronze_") || lower.endsWith("_bronze")) return "bronze";
  if (lower === "silver" || lower.startsWith("silver_") || lower.endsWith("_silver")) return "silver";
  if (lower === "gold" || lower.startsWith("gold_") || lower.endsWith("_gold")) return "gold";
  return null;
}

interface Props {
  catalog: string;
}

function SchemaListView({ catalog }: Props) {
  const allTables = useLineageStore((s) => s.allTables);
  const [filter, setFilter] = useState("");

  const schemaStats = useMemo(() => {
    const counts = new Map<string, number>();
    for (const t of allTables) {
      if (t.catalog !== catalog) continue;
      counts.set(t.schema, (counts.get(t.schema) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([schema, tableCount]) => ({ schema, tableCount, medallion: detectMedallion(schema) }))
      .sort((a, b) => {
        // Medallion order: bronze, silver, gold, then others alphabetical
        const order = { bronze: 0, silver: 1, gold: 2 };
        const ax = a.medallion ? order[a.medallion] : 99;
        const bx = b.medallion ? order[b.medallion] : 99;
        if (ax !== bx) return ax - bx;
        return a.schema.localeCompare(b.schema);
      });
  }, [allTables, catalog]);

  const filtered = useMemo(() => {
    if (!filter.trim()) return schemaStats;
    const q = filter.toLowerCase();
    return schemaStats.filter((s) => s.schema.toLowerCase().includes(q));
  }, [schemaStats, filter]);

  return (
    <PageShell>
      <div className="flex items-center justify-between mb-6">
        <div className="space-y-2">
          <Breadcrumb catalog={catalog} />
          <h2 className="text-[20px] font-semibold text-white tracking-tight">
            {catalog}
            <span className="text-slate-500 font-normal text-[14px] ml-2">
              ({schemaStats.length} schema{schemaStats.length !== 1 && "s"})
            </span>
          </h2>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => goCatalogLineage(catalog)}
            className="flex items-center gap-2 px-3.5 py-2 rounded-lg bg-accent/10 hover:bg-accent/20 border border-accent/25 text-accent-light text-[12px] font-medium transition-all duration-200 whitespace-nowrap"
            title="Visualize lineage for every table in this catalog"
          >
            <FolderTree size={14} />
            View catalog lineage
          </button>
          <div className="relative w-72">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter schemas..."
              className="w-full pl-9 pr-3 py-2 bg-surface-50/80 border border-white/[0.06] rounded-lg text-[13px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-accent/40 transition-colors font-mono"
            />
          </div>
        </div>
      </div>

      {schemaStats.length === 0 ? (
        <div className="flex items-center justify-center py-16">
          <p className="text-sm text-slate-500">No accessible schemas in this catalog.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="flex items-center justify-center py-16">
          <p className="text-sm text-slate-500">No schemas matching &ldquo;{filter}&rdquo;</p>
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((s, i) => {
            const style = s.medallion ? MEDALLION_STYLES[s.medallion] : null;
            return (
              <motion.div
                key={s.schema}
                initial={{ opacity: 0, x: -4 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: Math.min(i * 0.015, 0.1) }}
                className="group flex items-center gap-2 pl-5 pr-3 py-4 bg-surface-50/60 hover:bg-surface-50/90 border border-white/[0.06] hover:border-accent/30 rounded-xl transition-all duration-200"
              >
                <button
                  onClick={() => goTables(catalog, s.schema)}
                  className="flex items-center gap-4 flex-1 min-w-0 text-left"
                  title="Browse tables"
                >
                  <Layers size={16} className={style?.icon ?? "text-slate-500"} />
                  <span className="font-mono text-[14px] text-slate-200 font-medium truncate">{s.schema}</span>
                  {style && (
                    <span className={`text-[9px] font-medium tracking-wider uppercase px-1.5 py-0.5 rounded border ${style.badge}`}>
                      {s.medallion}
                    </span>
                  )}
                  <span className="ml-auto text-[12px] text-slate-500 font-mono whitespace-nowrap">
                    {s.tableCount.toLocaleString()} table{s.tableCount !== 1 && "s"}
                  </span>
                </button>
                <button
                  onClick={() => goSchemaLineage(catalog, s.schema)}
                  className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] hover:bg-accent/15 border border-white/[0.06] hover:border-accent/30 text-slate-400 hover:text-accent-light text-[11px] font-medium transition-all duration-200 whitespace-nowrap flex-shrink-0"
                  title="Visualize lineage for every table in this schema"
                >
                  <GitBranch size={12} />
                  Lineage
                </button>
              </motion.div>
            );
          })}
        </div>
      )}
    </PageShell>
  );
}

export default memo(SchemaListView);
