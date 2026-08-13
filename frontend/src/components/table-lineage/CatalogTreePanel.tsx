import { useMemo, useState } from "react";
import { ChevronRight, ChevronLeft, Database, Folder, Table2, Search } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import type { TableSearchItem } from "../../api/client";

/** Left-side catalog → schema → table tree, built entirely from the client-side
 *  `allTables` index already loaded into the store (no extra API calls). Clicking
 *  a table calls onSelect with its FQN.
 *
 *  Styled as a deep-maroon rail (a deliberate branded dark surface, fixed in
 *  both light and dark app themes) that collapses to a slim icon strip via the
 *  bottom control so the lineage graph can take the width. Because the maroon
 *  ground is theme-independent, the text/icon colors here are fixed rose tints
 *  rather than the theme-aware slate tokens used elsewhere. */

interface Props {
  selected: string | null;
  onSelect: (fqn: string) => void;
  /** Collapsed = slim icon rail. Owned by the workspace so it can drive the
   *  container width; both are optional so the panel renders standalone. */
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

type Tree = Map<string, Map<string, TableSearchItem[]>>;

export default function CatalogTreePanel({ selected, onSelect, collapsed = false, onToggleCollapse }: Props) {
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
    <div className="flex flex-col h-full bg-gradient-to-b from-[#4a0d17] to-[#29070f] text-[#fff1f2]">
      {collapsed ? (
        /* Slim icon rail — a single affordance that expands the tree again. */
        <button
          onClick={onToggleCollapse}
          title="Expand catalog"
          className="mx-auto mt-3 w-9 h-9 rounded-lg grid place-items-center text-[#fecdd3]/75 hover:text-[#fff1f2] hover:bg-white/10 transition-colors"
        >
          <Search size={16} />
        </button>
      ) : (
        <>
          {/* Filter */}
          <div className="p-3 border-b border-white/10">
            <div className="flex items-center gap-2 px-2.5 py-1.5 bg-black/25 border border-white/10 rounded-lg focus-within:border-rose-400/50">
              <Search size={13} className="text-[#fecdd3]/65" />
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter tables…"
                className="bg-transparent text-[12px] text-[#fff1f2] placeholder:text-[#fecdd3]/45 outline-none flex-1 min-w-0"
              />
            </div>
          </div>

          {/* Tree */}
          <div className="flex-1 overflow-y-auto py-1">
            {catalogs.length === 0 && (
              <div className="px-4 py-6 text-[11px] text-[#fecdd3]/55 text-center">No tables match.</div>
            )}
            {catalogs.map((cat) => {
              const schemas = tree.get(cat)!;
              const catOpen = filtering || expandedCat.has(cat);
              return (
                <div key={cat}>
                  <button
                    onClick={() => toggleCat(cat)}
                    className="w-full flex items-center gap-1.5 px-3 py-1.5 hover:bg-white/[0.06] transition-colors text-left"
                  >
                    <ChevronRight size={12} className={`text-[#fecdd3]/55 transition-transform shrink-0 ${catOpen ? "rotate-90" : ""}`} />
                    <Database size={13} className="text-[#fda4af] shrink-0" />
                    <span className="text-[12px] text-[#fff1f2] truncate">{cat}</span>
                  </button>

                  {catOpen && Array.from(schemas.keys()).sort().map((sch) => {
                    const key = `${cat}.${sch}`;
                    const tables = schemas.get(sch)!;
                    const schOpen = filtering || expandedSchema.has(key);
                    return (
                      <div key={key}>
                        <button
                          onClick={() => toggleSchema(key)}
                          className="w-full flex items-center gap-1.5 pl-7 pr-3 py-1.5 hover:bg-white/[0.06] transition-colors text-left"
                        >
                          <ChevronRight size={12} className={`text-[#fecdd3]/55 transition-transform shrink-0 ${schOpen ? "rotate-90" : ""}`} />
                          <Folder size={13} className="text-[#fecdd3]/85 shrink-0" />
                          <span className="text-[12px] text-[#ffe4e6] truncate">{sch}</span>
                          <span className="text-[10px] text-[#fecdd3]/45 ml-auto shrink-0">{tables.length}</span>
                        </button>

                        {schOpen && tables.slice().sort((a, b) => a.name.localeCompare(b.name)).map((t) => {
                          const isSel = selected === t.fqdn;
                          return (
                            <button
                              key={t.fqdn}
                              onClick={() => onSelect(t.fqdn)}
                              className={`w-full flex items-center gap-1.5 pl-[3.25rem] pr-3 py-1.5 transition-colors text-left ${
                                isSel
                                  ? "bg-rose-500/25 border-l-2 border-rose-400 shadow-[inset_0_0_12px_rgba(244,63,94,0.2)]"
                                  : "hover:bg-white/[0.06] border-l-2 border-transparent"
                              }`}
                            >
                              <Table2 size={12} className={isSel ? "text-[#fda4af] shrink-0" : "text-[#fecdd3]/55 shrink-0"} />
                              <span className={`text-[12px] truncate ${isSel ? "text-[#fff1f2] font-semibold" : "text-[#ffe4e6]/75"}`}>{t.name}</span>
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
        </>
      )}

      {/* Collapse toggle — pinned to the bottom, mirroring the reference nav. */}
      <button
        onClick={onToggleCollapse}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        className="flex items-center gap-2.5 px-4 py-3 border-t border-white/10 bg-black/20 text-[#fecdd3]/75 hover:text-[#fff1f2] text-[12px] transition-colors"
      >
        <ChevronLeft size={15} className={`shrink-0 transition-transform ${collapsed ? "rotate-180" : ""}`} />
        {!collapsed && <span>Collapse</span>}
      </button>
    </div>
  );
}
