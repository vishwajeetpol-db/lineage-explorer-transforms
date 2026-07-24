import { useMemo, useState } from "react";
import { ChevronRight, Database, Folder, Table2, Search } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

/** Left-side catalog → schema → table tree, built entirely from the client-side
 *  `allTables` index already loaded into the store (no extra API calls). Clicking
 *  a table calls onSelect with its FQN. */

interface Props {
  selected: string | null;
  onSelect: (fqn: string) => void;
}

type Tree = Map<string, Map<string, TableSearchItem[]>>;

export default function CatalogTreePanel({ selected, onSelect }: Props) {
  const allTables = useLineageStore((s) => s.allTables);
  const [expandedCat, setExpandedCat] = useState<Set<string>>(new Set());
  const [expandedSchema, setExpandedSchema] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");

  const tree: Tree = useMemo(() => {
    const t: Tree = new Map();
    const q = filter.trim().toLowerCase();
    for (const item of allTables) {
      if (q && !item.fqdn.toLowerCase().includes(q)) continue;
      if (!t.has(item.catalog)) t.set(item.catalog, new Map());
      const schemas = t.get(item.catalog)!;
      if (!schemas.has(item.schema)) schemas.set(item.schema, []);
      schemas.get(item.schema)!.push(item);
    }
    return t;
  }, [allTables, filter]);

  // When filtering, auto-expand everything so matches are visible.
  const filtering = filter.trim().length > 0;

  const toggleCat = (c: string) =>
    setExpandedCat((s) => { const n = new Set(s); n.has(c) ? n.delete(c) : n.add(c); return n; });
  const toggleSchema = (k: string) =>
    setExpandedSchema((s) => { const n = new Set(s); n.has(k) ? n.delete(k) : n.add(k); return n; });

  const catalogs = useMemo(() => Array.from(tree.keys()).sort(), [tree]);

  return (
    <div className="flex flex-col h-full">
      {/* Filter */}
      <div className="p-3 border-b border-white/[0.06]">
        <div className="flex items-center gap-2 px-2.5 py-1.5 bg-surface-100/60 border border-white/[0.08] rounded-lg focus-within:border-accent/40">
          <Search size={13} className="text-slate-500" />
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter tables…"
            className="bg-transparent text-[12px] text-slate-200 placeholder:text-slate-600 outline-none flex-1 min-w-0"
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {catalogs.length === 0 && (
          <div className="px-4 py-6 text-[11px] text-slate-600 text-center">No tables match.</div>
        )}
        {catalogs.map((cat) => {
          const schemas = tree.get(cat)!;
          const catOpen = filtering || expandedCat.has(cat);
          return (
            <div key={cat}>
              <button
                onClick={() => toggleCat(cat)}
                className="w-full flex items-center gap-1.5 px-3 py-1.5 hover:bg-white/[0.03] transition-colors text-left"
              >
                <ChevronRight size={12} className={`text-slate-600 transition-transform shrink-0 ${catOpen ? "rotate-90" : ""}`} />
                <Database size={13} className="text-indigo-400 shrink-0" />
                <span className="text-[12px] text-slate-200 truncate">{cat}</span>
              </button>

              {catOpen && Array.from(schemas.keys()).sort().map((sch) => {
                const key = `${cat}.${sch}`;
                const tables = schemas.get(sch)!;
                const schOpen = filtering || expandedSchema.has(key);
                return (
                  <div key={key}>
                    <button
                      onClick={() => toggleSchema(key)}
                      className="w-full flex items-center gap-1.5 pl-7 pr-3 py-1.5 hover:bg-white/[0.03] transition-colors text-left"
                    >
                      <ChevronRight size={12} className={`text-slate-600 transition-transform shrink-0 ${schOpen ? "rotate-90" : ""}`} />
                      <Folder size={13} className="text-sky-400 shrink-0" />
                      <span className="text-[12px] text-slate-300 truncate">{sch}</span>
                      <span className="text-[10px] text-slate-600 ml-auto shrink-0">{tables.length}</span>
                    </button>

                    {schOpen && tables.slice().sort((a, b) => a.name.localeCompare(b.name)).map((t) => {
                      const isSel = selected === t.fqdn;
                      return (
                        <button
                          key={t.fqdn}
                          onClick={() => onSelect(t.fqdn)}
                          className={`w-full flex items-center gap-1.5 pl-[3.25rem] pr-3 py-1.5 transition-colors text-left ${
                            isSel ? "bg-accent/15 border-l-2 border-accent" : "hover:bg-white/[0.03] border-l-2 border-transparent"
                          }`}
                        >
                          <Table2 size={12} className={isSel ? "text-accent-light shrink-0" : "text-slate-500 shrink-0"} />
                          <span className={`text-[12px] truncate ${isSel ? "text-accent-light" : "text-slate-400"}`}>{t.name}</span>
                        </button>
                      );
                    })}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
    </div>
  );
}
