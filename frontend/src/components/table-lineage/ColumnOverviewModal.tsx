import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { Sparkles, X, RefreshCw, Loader2, AlertCircle } from "lucide-react";
import { api, type ColumnOverviewResult, type OverviewColumn } from "../../api/client";
import { parseFqn } from "./panelShared";

/** Wide, centered overlay giving a plain-English LLM overview of a table's
 *  column transformations. Master–detail: pick a column on the left to see its
 *  source→transform→target graphic and explanation on the right. */

const CATEGORY_COLOR: Record<string, string> = {
  PASS_THROUGH: "bg-slate-500/15 text-slate-300 border-slate-500/25",
  CONSTANT: "bg-sky-500/15 text-sky-300 border-sky-500/25",
  CAST: "bg-cyan-500/15 text-cyan-300 border-cyan-500/25",
  ARITHMETIC: "bg-amber-500/15 text-amber-300 border-amber-500/25",
  AGGREGATE: "bg-orange-500/15 text-orange-300 border-orange-500/25",
  STRING: "bg-emerald-500/15 text-emerald-300 border-emerald-500/25",
  CONDITIONAL: "bg-violet-500/15 text-violet-300 border-violet-500/25",
  WINDOW: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/25",
  HASH: "bg-rose-500/15 text-rose-300 border-rose-500/25",
  UNKNOWN: "bg-surface-200 text-slate-400 border-white/[0.08]",
};

const colKey = (c: OverviewColumn) => c.target_column || c.column || "?";

/** A glowing, flowing edge (like the lineage graph's animated edges). */
function EdgeArrow() {
  return (
    <svg width="48" height="26" viewBox="0 0 48 26" className="self-center shrink-0 overflow-visible"
      style={{ filter: "drop-shadow(0 0 5px rgba(167,139,250,0.75))" }} aria-hidden="true">
      <line x1="0" y1="13" x2="38" y2="13" stroke="#a78bfa" strokeWidth="2" strokeLinecap="round" className="animated-edge" />
      <polygon points="37,7 48,13 37,19" fill="#a78bfa" />
    </svg>
  );
}

/** A lineage-graph-style node card: rounded, gradient fill, colored border,
 *  soft shadow, a status dot, and the FULL (wrapping) column name. */
function GraphNode({ name, tone }: { name: string; tone: "source" | "target" }) {
  const cls = tone === "source"
    ? "border-sky-500/40 text-sky-100 shadow-[0_2px_16px_rgba(56,189,248,0.15)]"
    : "border-emerald-500/40 text-emerald-100 shadow-[0_2px_16px_rgba(52,211,153,0.15)]";
  const dot = tone === "source" ? "bg-sky-400" : "bg-emerald-400";
  return (
    <div className={`flex items-center gap-2 rounded-xl border bg-gradient-to-br from-[#161625] to-[#12121e] px-2.5 py-2 ${cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
      <span className="font-mono text-[11px] break-all leading-snug">{name}</span>
    </div>
  );
}

/** The source-columns → transformation → target-column diagram for one column,
 *  styled like the main lineage graph: node cards + glowing flowing edges. Full
 *  source/target names (no truncation). */
function TransformGraphic({ col }: { col: OverviewColumn }) {
  const target = colKey(col);
  const sources = col.source_columns || [];
  const expr = col.expression || col.transformation || "";
  const cat = (col.category || "").toUpperCase();
  const catCls = CATEGORY_COLOR[cat] || CATEGORY_COLOR.UNKNOWN;

  const StageLabel = ({ children, color }: { children: React.ReactNode; color: string }) => (
    <div className={`text-[9px] uppercase tracking-wider mb-1.5 ${color}`}>{children}</div>
  );

  return (
    <div className="rounded-xl border border-white/[0.06] bg-[radial-gradient(circle_at_1px_1px,rgba(255,255,255,0.04)_1px,transparent_0)] [background-size:18px_18px] bg-surface-100/20 p-4">
      <div className="flex items-stretch gap-1">
        {/* Source columns (sky) — each source gets its own arrow into the
            transformation box, mirroring the lineage graph's per-edge fan-in. */}
        <div className="flex-1 min-w-0">
          <StageLabel color="text-sky-300/80">Source{sources.length !== 1 ? "s" : ""}</StageLabel>
          <div className="flex flex-col justify-center gap-2 min-h-full">
            {sources.length > 0 ? (
              sources.map((s, i) => (
                <div key={i} className="flex items-center gap-1">
                  <div className="flex-1 min-w-0"><GraphNode name={s} tone="source" /></div>
                  <EdgeArrow />
                </div>
              ))
            ) : (
              <div className="flex items-center gap-1">
                <div className="flex-1 min-w-0 text-[10px] text-slate-500 leading-snug px-2.5 py-2 rounded-xl border border-dashed border-white/[0.14]">constant / generated<br />(no source column)</div>
                <EdgeArrow />
              </div>
            )}
          </div>
        </div>

        {/* Transformation node (violet) */}
        <div className="flex-[1.3] min-w-0 self-center">
          <StageLabel color="text-violet-300/80">Transformation</StageLabel>
          <div className="rounded-xl border border-violet-500/50 bg-gradient-to-br from-violet-500/[0.16] to-fuchsia-500/[0.08] px-3 py-2.5 space-y-1.5 shadow-[0_2px_20px_rgba(167,139,250,0.2)]">
            <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wide ${catCls}`}>{cat || "TRANSFORM"}</span>
            <div className="font-mono text-[11px] text-slate-100 break-words leading-snug">{expr || "—"}</div>
          </div>
        </div>

        <EdgeArrow />

        {/* Target column (emerald) */}
        <div className="flex-1 min-w-0 self-center">
          <StageLabel color="text-emerald-300/80">Target</StageLabel>
          <GraphNode name={target} tone="target" />
        </div>
      </div>
    </div>
  );
}

export default function ColumnOverviewModal({
  table, entityType, entityId, onClose,
}: {
  table: string;
  entityType?: string;
  entityId?: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<ColumnOverviewResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  const load = useCallback(async (refresh = false) => {
    const parts = parseFqn(table);
    if (!parts) { setError("Invalid table."); setLoading(false); return; }
    setLoading(true); setError(null);
    try {
      const r = await api.getColumnTransformationOverview({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        entity_type: entityId ? entityType : undefined, entity_id: entityId || undefined,
        refresh,
      });
      setData(r);
      setSelected((cur) => cur ?? (r.columns[0] ? colKey(r.columns[0]) : null));
    } catch (e: any) {
      setError(e?.message || "Failed to generate overview");
    } finally { setLoading(false); }
  }, [table, entityType, entityId]);

  useEffect(() => { load(false); }, [load]);

  // Close on Escape.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const selectedCol = data?.columns.find((c) => colKey(c) === selected) || null;

  // Portal to <body> — the draggable panel that renders this uses backdrop-blur,
  // which would otherwise make `position: fixed` resolve against the panel (not
  // the viewport) and trap/clip the modal inside it.
  return createPortal(
    <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className="relative w-full max-w-4xl max-h-[85vh] flex flex-col rounded-2xl border border-white/[0.1] bg-surface-50 shadow-[0_24px_80px_rgba(0,0,0,0.6)] overflow-hidden">
        {/* Accent edge */}
        <div className="h-[3px] shrink-0 bg-gradient-to-r from-violet-500/80 to-transparent" />
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06] shrink-0">
          <span className="w-6 h-6 rounded-lg grid place-items-center bg-violet-500/15 shrink-0">
            <Sparkles size={13} className="text-violet-400" />
          </span>
          <span className="text-[13px] font-semibold text-slate-100">AI Column Transformation Overview</span>
          <span className="font-mono text-[10px] text-slate-500 truncate hidden sm:block">{table}</span>
          <button onClick={() => load(true)} disabled={loading} title="Regenerate (re-run the LLM)"
            className="ml-auto flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg bg-surface-100/60 hover:bg-white/[0.06] border border-white/[0.08] text-slate-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed">
            <RefreshCw size={11} className={loading ? "animate-spin" : ""} /> Regenerate
          </button>
          <button onClick={onClose} aria-label="Close overview" className="p-1 rounded text-slate-500 hover:text-slate-200 hover:bg-white/10 transition-colors shrink-0">
            <X size={16} />
          </button>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center gap-3 py-24 text-slate-500">
            <Loader2 size={24} className="animate-spin text-violet-400" />
            <span className="text-[12px]">Generating overview with the LLM…</span>
          </div>
        ) : error ? (
          <div className="flex flex-col items-center justify-center gap-3 py-24 px-6 text-center">
            <AlertCircle size={22} className="text-rose-400" />
            <span className="text-[12px] text-rose-300 max-w-md break-words">{error}</span>
            <button onClick={() => load(true)} className="text-[11px] px-3 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200">Retry</button>
          </div>
        ) : (
          <div className="flex-1 flex flex-col overflow-hidden">
            {data?.error && (
              <div className="mx-4 mt-3 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2">
                The LLM overview couldn’t be generated ({data.error}). Showing the columns without explanations.
              </div>
            )}
            {/* Summary */}
            {data?.summary && (
              <div className="px-4 py-3 border-b border-white/[0.06] shrink-0">
                <div className="text-[10px] uppercase tracking-wider text-slate-500 mb-1">Summary</div>
                <p className="text-[12px] text-slate-300 leading-relaxed">{data.summary}</p>
              </div>
            )}
            {/* Master–detail */}
            <div className="flex-1 flex overflow-hidden min-h-0">
              {/* Column list */}
              <div className="w-[220px] shrink-0 border-r border-white/[0.06] overflow-y-auto">
                {(data?.columns || []).map((c) => {
                  const key = colKey(c);
                  const cat = (c.category || "").toUpperCase();
                  const active = key === selected;
                  return (
                    <button key={key} onClick={() => setSelected(key)}
                      className={`w-full flex items-center gap-2 px-3 py-2 text-left border-l-2 transition-colors ${
                        active ? "bg-violet-500/[0.12] border-violet-400" : "border-transparent hover:bg-white/[0.04]"
                      }`}>
                      <span className={`font-mono text-[11px] truncate flex-1 ${active ? "text-violet-900 dark:text-violet-100" : "text-slate-300"}`}>{key}</span>
                      {cat && <span className="text-[8px] uppercase tracking-wide text-slate-500 shrink-0">{cat}</span>}
                    </button>
                  );
                })}
                {(data?.columns || []).length === 0 && (
                  <div className="px-3 py-4 text-[11px] text-slate-600">No columns resolved.</div>
                )}
              </div>

              {/* Detail */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4 min-w-0">
                {selectedCol ? (
                  <>
                    <TransformGraphic col={selectedCol} />
                    <div className="rounded-xl border border-white/[0.06] bg-surface-100/40 px-3.5 py-3">
                      <div className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-violet-300 mb-1.5">
                        <Sparkles size={11} /> Explanation
                      </div>
                      <p className="text-[12px] text-slate-300 leading-relaxed">
                        {selectedCol.explanation || "No explanation available for this column."}
                      </p>
                    </div>
                  </>
                ) : (
                  <div className="h-full flex items-center justify-center text-[12px] text-slate-600">
                    Select a column to see how it’s derived.
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
