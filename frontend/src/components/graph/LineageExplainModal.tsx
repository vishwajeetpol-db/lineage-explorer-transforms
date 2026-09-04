import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { motion, AnimatePresence } from "framer-motion";
import { Lightbulb, X, RefreshCw, Loader2, AlertCircle, ArrowRight } from "lucide-react";
import { api, type LineageExplainResult } from "../../api/client";

/**
 * A portaled modal that shows an AI-generated, plain-English explanation of the
 * CURRENT lineage graph (the Business-view lightbulb). The parent passes the
 * on-screen (business-view) nodes + edges so the narrative matches exactly what
 * the user sees. Results are cached per (focusTable · detail · graph signature)
 * for the lifetime of the component so re-opening is instant; the refresh button
 * forces a fresh call.
 */

export type ExplainNode = { id: string; label: string; type: string };
export type ExplainEdge = { source: string; target: string };

interface Props {
  open: boolean;
  onClose: () => void;
  focusTable: string;
  nodes: ExplainNode[];
  edges: ExplainEdge[];
  detail: "data" | "data_and_processing";
}

function signature(focusTable: string, detail: string, nodes: ExplainNode[], edges: ExplainEdge[]): string {
  return `${focusTable}|${detail}|${nodes.length}n|${edges.length}e|${nodes.map((n) => n.id).join(",")}`;
}

export default function LineageExplainModal({ open, onClose, focusTable, nodes, edges, detail }: Props) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<LineageExplainResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cache = useRef<Map<string, LineageExplainResult>>(new Map());

  const load = useCallback(
    async (refresh: boolean) => {
      const sig = signature(focusTable, detail, nodes, edges);
      if (!refresh && cache.current.has(sig)) {
        setResult(cache.current.get(sig)!);
        setError(null);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        const out = await api.explainLineageGraph({ focus_table: focusTable, nodes, edges, detail });
        if (out.error && !out.summary) {
          setError(out.error);
        } else {
          cache.current.set(sig, out);
          setResult(out);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    },
    [focusTable, detail, nodes, edges]
  );

  useEffect(() => {
    if (open) load(false);
  }, [open, load]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return createPortal(
    <AnimatePresence>
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        exit={{ opacity: 0 }}
        className="fixed inset-0 z-[2000] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
        onClick={onClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.96, y: 8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.96, y: 8 }}
          transition={{ duration: 0.2 }}
          onClick={(e) => e.stopPropagation()}
          className="w-full max-w-2xl max-h-[85vh] overflow-hidden flex flex-col rounded-2xl border border-white/[0.08] bg-surface-100 shadow-[0_24px_80px_rgba(0,0,0,0.6)]"
        >
          {/* Header */}
          <div className="flex items-center gap-2.5 px-5 py-4 border-b border-white/[0.06]">
            <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-amber-500/15 text-amber-300">
              <Lightbulb size={16} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-[13px] font-semibold text-slate-100">How this data flows</div>
              <div className="text-[11px] text-slate-500 truncate">Plain-English explanation of {focusTable}</div>
            </div>
            <button
              onClick={() => load(true)}
              title="Regenerate"
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-white/[0.06] transition-colors"
            >
              <RefreshCw size={14} className={loading ? "animate-spin" : ""} />
            </button>
            <button
              onClick={onClose}
              title="Close"
              className="p-1.5 rounded-lg text-slate-500 hover:text-slate-200 hover:bg-white/[0.06] transition-colors"
            >
              <X size={16} />
            </button>
          </div>

          {/* Body */}
          <div className="overflow-y-auto px-5 py-4">
            {loading && (
              <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-400">
                <Loader2 size={26} className="animate-spin text-amber-300" />
                <span className="text-[12px]">Reading the lineage and writing a plain-English summary…</span>
              </div>
            )}

            {!loading && error && (
              <div className="flex items-start gap-2.5 rounded-xl border border-amber-500/25 bg-amber-500/[0.08] px-4 py-3 text-amber-200">
                <AlertCircle size={16} className="mt-0.5 shrink-0" />
                <div className="text-[12px] leading-snug">
                  <div className="font-medium mb-0.5">Couldn't generate an explanation</div>
                  <div className="text-amber-200/80">{error}</div>
                </div>
              </div>
            )}

            {!loading && !error && result && (
              <div className="space-y-5">
                {result.summary && (
                  <p className="text-[13px] leading-relaxed text-slate-200">{result.summary}</p>
                )}

                {result.steps && result.steps.length > 0 && (
                  <ol className="space-y-2.5">
                    {result.steps.map((s, i) => (
                      <li key={i} className="flex gap-3">
                        <div className="flex flex-col items-center">
                          <div className="flex items-center justify-center w-6 h-6 rounded-full bg-emerald-500/15 text-emerald-300 text-[11px] font-semibold shrink-0">
                            {i + 1}
                          </div>
                          {i < result.steps.length - 1 && <div className="w-px flex-1 bg-white/[0.08] my-1" />}
                        </div>
                        <div className="pb-1.5">
                          <div className="flex items-center gap-1.5 text-[12px] font-medium text-slate-100">
                            {s.title}
                          </div>
                          <div className="text-[12px] leading-snug text-slate-400 mt-0.5">{s.detail}</div>
                        </div>
                      </li>
                    ))}
                  </ol>
                )}

                {!result.summary && (!result.steps || result.steps.length === 0) && (
                  <div className="py-10 text-center text-[12px] text-slate-500">
                    No explanation was produced for this graph.
                  </div>
                )}

                <div className="flex items-center gap-1.5 pt-2 border-t border-white/[0.06] text-[10px] text-slate-600">
                  <ArrowRight size={11} />
                  AI-generated from the current view — verify against the graph before relying on it.
                </div>
              </div>
            )}
          </div>
        </motion.div>
      </motion.div>
    </AnimatePresence>,
    document.body
  );
}
