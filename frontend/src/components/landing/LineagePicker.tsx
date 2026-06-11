import { memo, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { Search, Layers, FolderTree, ChevronRight, X } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { goSchemaLineage, goCatalogLineage } from "../../hooks/useRouter";

interface Props {
  mode: "schema" | "catalog";
  onClose: () => void;
}

/**
 * Modal picker for whole-schema / whole-catalog lineage. Derives its options
 * from the already-loaded table index (no extra API calls).
 *
 * - "catalog" mode lists catalogs (with schema/table counts).
 * - "schema" mode lists every catalog.schema pair (with table counts).
 */
function LineagePicker({ mode, onClose }: Props) {
  const allTables = useLineageStore((s) => s.allTables);
  const [filter, setFilter] = useState("");

  // Close on Escape
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const catalogOptions = useMemo(() => {
    const byCatalog = new Map<string, { schemas: Set<string>; tables: number }>();
    for (const t of allTables) {
      const entry = byCatalog.get(t.catalog) ?? { schemas: new Set(), tables: 0 };
      entry.schemas.add(t.schema);
      entry.tables += 1;
      byCatalog.set(t.catalog, entry);
    }
    return Array.from(byCatalog.entries())
      .map(([catalog, { schemas, tables }]) => ({ catalog, schemaCount: schemas.size, tableCount: tables }))
      .sort((a, b) => a.catalog.localeCompare(b.catalog));
  }, [allTables]);

  const schemaOptions = useMemo(() => {
    const byPair = new Map<string, { catalog: string; schema: string; tables: number }>();
    for (const t of allTables) {
      const key = `${t.catalog}.${t.schema}`;
      const entry = byPair.get(key) ?? { catalog: t.catalog, schema: t.schema, tables: 0 };
      entry.tables += 1;
      byPair.set(key, entry);
    }
    return Array.from(byPair.values()).sort(
      (a, b) => a.catalog.localeCompare(b.catalog) || a.schema.localeCompare(b.schema)
    );
  }, [allTables]);

  const filteredCatalogs = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return catalogOptions;
    return catalogOptions.filter((c) => c.catalog.toLowerCase().includes(q));
  }, [catalogOptions, filter]);

  const filteredSchemas = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return schemaOptions;
    return schemaOptions.filter((s) => `${s.catalog}.${s.schema}`.toLowerCase().includes(q));
  }, [schemaOptions, filter]);

  const isCatalog = mode === "catalog";

  return (
    <div className="fixed inset-0 z-[120] flex items-start justify-center pt-[12vh] px-4" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <motion.div
        initial={{ opacity: 0, y: -8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        transition={{ duration: 0.18 }}
        onClick={(e) => e.stopPropagation()}
        className="relative w-full max-w-lg bg-[#14141F] border border-white/[0.08] rounded-2xl shadow-[0_24px_64px_rgba(0,0,0,0.6)] overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center gap-3 px-5 py-4 border-b border-white/[0.06]">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-indigo-500/25 to-purple-500/25 flex items-center justify-center">
            {isCatalog ? <FolderTree size={17} className="text-indigo-400" /> : <Layers size={17} className="text-indigo-400" />}
          </div>
          <div className="flex-1">
            <div className="text-[14px] font-semibold text-slate-100">
              {isCatalog ? "Catalog lineage" : "Schema lineage"}
            </div>
            <div className="text-[11px] text-slate-500">
              {isCatalog ? "Select a catalog to map all of its tables" : "Select a schema to map all of its tables"}
            </div>
          </div>
          <button
            onClick={onClose}
            className="flex items-center justify-center w-7 h-7 rounded-lg hover:bg-white/[0.06] text-slate-500 hover:text-slate-300 transition-colors"
            aria-label="Close"
          >
            <X size={15} />
          </button>
        </div>

        {/* Search */}
        <div className="px-5 pt-4">
          <div className="relative">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              autoFocus
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder={isCatalog ? "Filter catalogs..." : "Filter schemas..."}
              className="w-full pl-9 pr-3 py-2 bg-white/[0.03] border border-white/[0.06] rounded-lg text-[13px] text-slate-200 placeholder:text-slate-600 outline-none focus:border-accent/40 transition-colors font-mono"
            />
          </div>
        </div>

        {/* Options */}
        <div className="px-3 py-3 max-h-[42vh] overflow-y-auto">
          {isCatalog ? (
            filteredCatalogs.length === 0 ? (
              <p className="text-center text-[13px] text-slate-500 py-8">No catalogs found</p>
            ) : (
              filteredCatalogs.map((c) => (
                <button
                  key={c.catalog}
                  onClick={() => { goCatalogLineage(c.catalog); onClose(); }}
                  className="group w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-accent/[0.08] transition-colors text-left"
                >
                  <FolderTree size={14} className="text-indigo-400 flex-shrink-0" />
                  <span className="font-mono text-[13px] text-slate-200 flex-1 truncate">{c.catalog}</span>
                  <span className="text-[11px] text-slate-500 font-mono">
                    {c.schemaCount} schema{c.schemaCount !== 1 && "s"} · {c.tableCount.toLocaleString()} tables
                  </span>
                  <ChevronRight size={13} className="text-slate-600 group-hover:text-accent-light transition-colors" />
                </button>
              ))
            )
          ) : filteredSchemas.length === 0 ? (
            <p className="text-center text-[13px] text-slate-500 py-8">No schemas found</p>
          ) : (
            filteredSchemas.map((s) => (
              <button
                key={`${s.catalog}.${s.schema}`}
                onClick={() => { goSchemaLineage(s.catalog, s.schema); onClose(); }}
                className="group w-full flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-accent/[0.08] transition-colors text-left"
              >
                <Layers size={14} className="text-indigo-400 flex-shrink-0" />
                <span className="font-mono text-[13px] text-slate-200 flex-1 truncate">
                  {s.catalog}.<span className="text-slate-100">{s.schema}</span>
                </span>
                <span className="text-[11px] text-slate-500 font-mono">{s.tables.toLocaleString()} tables</span>
                <ChevronRight size={13} className="text-slate-600 group-hover:text-accent-light transition-colors" />
              </button>
            ))
          )}
        </div>
      </motion.div>
    </div>
  );
}

export default memo(LineagePicker);
