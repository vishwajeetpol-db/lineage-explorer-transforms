import { useCallback, useEffect, useMemo, useState } from "react";
import {
  GitFork, Play, Loader2, RefreshCw, GitCompare, AlertTriangle, History,
  Cpu, Sparkles, Layers, ArrowRight, CheckCircle2,
} from "lucide-react";
import {
  api,
  type ColumnTransformResult,
  type AnalyzeProducerColumn,
  type TransformVersion,
  type CrossSourceCompare,
} from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";
import { NoTable, SectionTitle, parseFqn } from "./panelShared";

const ENTITY_TYPES = ["NOTEBOOK", "JOB", "PIPELINE", "QUERY"];

// The precedence chain, best-source-first — shown as a legend so users
// understand where the answer came from.
const SOURCE_META: Record<string, { label: string; icon: typeof Cpu; color: string; blurb: string }> = {
  plan_capture: { label: "Captured Spark plan", icon: Cpu, color: "text-emerald-400", blurb: "Exact, deterministic — from the offline lineage-capture wheel. No LLM." },
  cdc_spec: { label: "apply_changes / CDC spec", icon: Layers, color: "text-sky-400", blurb: "Passthrough columns from the captured AUTO-CDC spec." },
  stored: { label: "Stored LLM analysis", icon: History, color: "text-violet-400", blurb: "Cached from a prior LLM run — no new cost." },
  llm: { label: "Fresh LLM analysis", icon: Sparkles, color: "text-violet-400", blurb: "Just inferred by the workspace LLM from producer source." },
  unavailable: { label: "Unavailable", icon: AlertTriangle, color: "text-amber-400", blurb: "" },
  none: { label: "No lineage yet", icon: AlertTriangle, color: "text-slate-500", blurb: "" },
};

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

function ColumnCard({ c }: { c: AnalyzeProducerColumn }) {
  const cat = (c.category || "").toUpperCase();
  const catCls = CATEGORY_COLOR[cat] || CATEGORY_COLOR.UNKNOWN;
  const expr = c.expression || c.transformation;
  return (
    <div className="px-3 py-2.5 bg-surface-100/40 space-y-1.5">
      <div className="flex items-center gap-2">
        <GitFork size={12} className="text-violet-400 shrink-0 rotate-90" />
        <span className="font-mono text-[12px] text-slate-100 font-medium truncate">{c.target_column || c.column || "?"}</span>
        {cat && <span className={`text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wide ${catCls}`}>{cat}</span>}
        {c.confidence != null && <span className="text-[9px] text-slate-500 ml-auto shrink-0">conf {String(c.confidence)}</span>}
      </div>
      {c.source_columns && c.source_columns.length > 0 && (
        <div className="flex items-center gap-1.5 text-[10px] text-slate-500 flex-wrap">
          {c.source_columns.slice(0, 6).map((s, i) => (
            <span key={i} className="font-mono px-1.5 py-0.5 rounded bg-surface-200/70 text-slate-400">{s}</span>
          ))}
          <ArrowRight size={10} className="text-slate-600" />
          <span className="font-mono text-violet-300">{c.target_column || c.column}</span>
        </div>
      )}
      {expr && <div className="font-mono text-[10px] text-slate-400 break-words bg-black/20 rounded px-2 py-1">{expr}</div>}
    </div>
  );
}

export default function ColumnTransformationPanel({ table }: { table: string | null }) {
  const nodes = useLineageStore((s) => s.nodes);
  const entityNodes = useMemo(
    () => nodes.filter((n): n is Extract<typeof n, { node_type: "entity" }> => n.node_type === "entity"),
    [nodes],
  );

  const [entityType, setEntityType] = useState("PIPELINE");
  const [entityId, setEntityId] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [data, setData] = useState<ColumnTransformResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Unified version list across sources (captured plans + LLM) and cross-source compare.
  const [allVersions, setAllVersions] = useState<TransformVersion[]>([]);
  const [compareFrom, setCompareFrom] = useState<string>("");
  const [compareTo, setCompareTo] = useState<string>("");
  const [compareData, setCompareData] = useState<CrossSourceCompare | null>(null);
  const [comparing, setComparing] = useState(false);

  const parts = parseFqn(table);

  useEffect(() => {
    api.getAnalyzeModels()
      .then((r) => { setModels(r.models); setModel((m) => m || r.default); })
      .catch(() => { setModels(["databricks-claude-sonnet-4-6"]); setModel((m) => m || "databricks-claude-sonnet-4-6"); });
  }, []);

  // Refresh the unified version list (captured + LLM) for the current table/producer.
  const loadVersions = useCallback(async (et?: string, eid?: string) => {
    if (!parts) return;
    try {
      const r = await api.listColumnTransformationVersions({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        entity_type: et ?? (eid ?? entityId ? (et ?? entityType) : undefined),
        entity_id: eid ?? (entityId || undefined),
      });
      setAllVersions(r.versions);
    } catch { setAllVersions([]); }
  }, [table, entityType, entityId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-resolve on table change — the offline captured plan (if any) shows with
  // no producer/LLM needed. Runs the precedence chain.
  const resolve = useCallback(async (opts?: { et?: string; eid?: string; force?: boolean }) => {
    if (!parts) return;
    setLoading(true); setError(null); setCompareData(null);
    try {
      const r = await api.resolveColumnTransformations({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        entity_type: opts?.et ?? (entityId ? entityType : undefined),
        entity_id: opts?.eid ?? (entityId || undefined),
        force_rerun: opts?.force || false,
        model: opts?.force ? (model || undefined) : undefined,
      });
      setData(r);
      loadVersions(opts?.et ?? (entityId ? entityType : undefined), opts?.eid ?? (entityId || undefined));
    } catch (e: any) {
      setError(e.message || "Failed to resolve column transformations");
    } finally { setLoading(false); }
  }, [table, entityType, entityId, model, loadVersions]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setData(null); setError(null); setEntityId(""); setCompareData(null); setAllVersions([]); }, [table]);
  // Kick off an initial resolve (captured plan / stored) whenever the table changes.
  useEffect(() => { if (parts) resolve(); }, [table]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickProducer = (et: string, eid: string) => {
    setEntityType(et); setEntityId(eid);
    resolve({ et, eid });
  };

  const runCompare = async () => {
    if (!compareFrom || !compareTo || compareFrom === compareTo || !parts) return;
    setComparing(true); setError(null);
    try {
      const c = await api.compareTransformationVersions({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        ref_from: compareFrom, ref_to: compareTo,
        entity_type: entityId ? entityType : undefined,
        entity_id: entityId || undefined,
      });
      if (c.error) setError(c.error); else setCompareData(c);
    } catch (e: any) {
      setError(e.message || "Compare failed");
    } finally { setComparing(false); }
  };

  if (!table) return <NoTable />;

  const src = data?.source || "none";
  const meta = SOURCE_META[src] || SOURCE_META.none;
  const SrcIcon = meta.icon;
  const isLLM = src === "stored" || src === "llm";
  const canReanalyze = isLLM || (data?.source === "none" && !!entityId);

  return (
    <div className="space-y-4">
      {/* Intro / precedence explainer */}
      <div className="rounded-xl bg-gradient-to-br from-violet-500/[0.08] to-emerald-500/[0.05] border border-white/[0.08] px-4 py-3">
        <div className="flex items-center gap-2 text-[12px] font-semibold text-slate-100">
          <GitFork size={14} className="text-violet-400 rotate-90" /> Column Transformation Lineage
        </div>
        <p className="text-[11px] text-slate-500 mt-1">
          How each column was derived — resolved best-source-first:
        </p>
        <div className="flex items-center gap-1 mt-2 flex-wrap text-[9px]">
          <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/25">Captured plan</span>
          <ArrowRight size={9} className="text-slate-600" />
          <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/25">CDC spec</span>
          <ArrowRight size={9} className="text-slate-600" />
          <span className="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-300 border border-violet-500/25">Stored LLM</span>
          <ArrowRight size={9} className="text-slate-600" />
          <span className="px-1.5 py-0.5 rounded bg-fuchsia-500/15 text-fuchsia-300 border border-fuchsia-500/25">Fresh LLM</span>
        </div>
      </div>

      {loading && (
        <div className="flex items-center justify-center gap-2 py-10 text-slate-500">
          <Loader2 size={18} className="animate-spin text-accent" /> <span className="text-[12px]">Resolving…</span>
        </div>
      )}

      {error && (
        <div className="text-[11px] text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-2 break-words">{error}</div>
      )}

      {/* Source-of-truth banner */}
      {data && !loading && (
        <div className={`rounded-xl border px-3.5 py-3 ${
          src === "plan_capture" ? "bg-emerald-500/[0.07] border-emerald-500/25"
          : src === "cdc_spec" ? "bg-sky-500/[0.07] border-sky-500/25"
          : isLLM ? "bg-violet-500/[0.07] border-violet-500/25"
          : "bg-surface-100/50 border-white/[0.08]"
        }`}>
          <div className="flex items-center gap-2">
            <SrcIcon size={15} className={meta.color} />
            <span className="text-[12px] font-semibold text-slate-100">{data.source_label || meta.label}</span>
            {src === "plan_capture" && <CheckCircle2 size={13} className="text-emerald-400 ml-auto" />}
            {data.version != null && <span className="text-[10px] text-slate-500 ml-auto">v{data.version}</span>}
          </div>
          {meta.blurb && <p className="text-[10px] text-slate-500 mt-1">{meta.blurb}</p>}
          {(data.captured_at || data.analyzed_at) && (
            <p className="text-[10px] text-slate-600 mt-0.5">{(data.captured_at || data.analyzed_at || "").slice(0, 19).replace("T", " ")}</p>
          )}
          {data.stale && (
            <div className="flex items-center gap-1.5 text-[10px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-2 py-1 mt-2">
              <AlertTriangle size={11} /> Producer source changed since this analysis — re-analyze to refresh.
            </div>
          )}
        </div>
      )}

      {/* CDC spec detail */}
      {data?.source === "cdc_spec" && data.cdc_spec && (
        <div className="rounded-xl border border-white/[0.06] bg-surface-100/40 px-3 py-2.5 text-[11px] text-slate-300 space-y-1">
          <div><span className="text-slate-500">Source:</span> <span className="font-mono">{data.cdc_spec.source || "—"}</span></div>
          <div><span className="text-slate-500">Keys:</span> <span className="font-mono">{JSON.stringify(data.cdc_spec.keys)}</span></div>
          <div><span className="text-slate-500">Sequence by:</span> <span className="font-mono">{data.cdc_spec.sequence_by || "—"}</span> · <span className="text-slate-500">SCD</span> {String(data.cdc_spec.scd_type)}</div>
        </div>
      )}

      {/* Columns */}
      {data && data.columns.length > 0 && (
        <div>
          <SectionTitle>Columns ({data.columns.length})</SectionTitle>
          <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
            {data.columns.map((c, i) => <ColumnCard key={i} c={c} />)}
          </div>
        </div>
      )}

      {data && data.columns.length === 0 && !loading && data.source !== "cdc_spec" && (
        <div className="text-[11px] text-slate-500 py-2">
          {data.detail || "No column transformations resolved. Pick a producer below to run LLM analysis."}
        </div>
      )}

      {/* Producer + model — only needed for the LLM path */}
      <div className="rounded-xl bg-surface-100/40 border border-white/[0.06] px-3 py-3 space-y-2.5">
        <div className="text-[11px] font-medium text-slate-300 flex items-center gap-1.5">
          <Sparkles size={12} className="text-violet-400" /> LLM analysis (fallback / refresh)
        </div>
        {entityNodes.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {entityNodes.map((e) => (
              <button key={e.id} onClick={() => pickProducer(e.entity_type || "PIPELINE", e.entity_id)}
                className={`text-[11px] px-2 py-1 rounded-lg border transition-colors ${
                  entityId === e.entity_id ? "bg-violet-500/20 border-violet-500/40 text-violet-200"
                  : "bg-surface-100/60 border-white/[0.06] text-slate-300 hover:border-violet-500/30"
                }`}>
                {e.display_name || `${e.entity_type} ${e.entity_id}`}
              </button>
            ))}
          </div>
        )}
        <div className="flex gap-1.5">
          <select value={entityType} onChange={(e) => setEntityType(e.target.value)}
            className="px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 outline-none focus:border-accent/50">
            {ENTITY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input value={entityId} onChange={(e) => setEntityId(e.target.value)} placeholder="entity id"
            className="flex-1 min-w-0 px-2.5 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 font-mono placeholder:text-slate-600 outline-none focus:border-accent/50" />
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 shrink-0">Model</span>
          <select value={model} onChange={(e) => setModel(e.target.value)}
            className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 font-mono outline-none focus:border-accent/50">
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button onClick={() => resolve({ et: entityType, eid: entityId, force: true })}
          disabled={loading || !entityId.trim()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200 text-[12px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed">
          {loading ? <Loader2 size={13} className="animate-spin" /> : canReanalyze ? <RefreshCw size={13} /> : <Play size={13} />}
          {loading ? "Working…" : canReanalyze ? "Re-analyze with LLM (new version)" : "Analyze with LLM"}
        </button>
      </div>

      {/* Unified version history + cross-source compare */}
      {allVersions.length > 0 && (
        <div className="rounded-xl bg-surface-100/40 border border-white/[0.06] px-3 py-3 space-y-2.5">
          <div className="flex items-center gap-1.5 text-[11px] text-slate-300">
            <History size={13} className="text-cyan-400" /> Version history ({allVersions.length})
          </div>

          {/* All versions, source-tagged */}
          <div className="rounded-lg border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04] max-h-40 overflow-y-auto">
            {allVersions.map((v) => {
              const isPlan = v.source === "plan_capture";
              return (
                <div key={v.ref} className="flex items-center gap-2 px-2.5 py-1.5 bg-surface-100/40">
                  <span className={`text-[8px] px-1.5 py-0.5 rounded border uppercase tracking-wide shrink-0 ${
                    isPlan ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/25"
                           : "bg-violet-500/15 text-violet-300 border-violet-500/25"}`}>
                    {isPlan ? "plan" : "llm"}
                  </span>
                  <span className="text-[11px] text-slate-200 truncate flex-1">{v.label}</span>
                  {v.analyzed_at && <span className="text-[9px] text-slate-600 shrink-0">{v.analyzed_at.slice(0, 10)}</span>}
                </div>
              );
            })}
          </div>

          {/* Cross-source compare (only meaningful with 2+ versions) */}
          {allVersions.length > 1 && (
            <>
              <div className="flex items-center gap-1.5 text-[10px] text-slate-500 pt-1">
                <GitCompare size={12} className="text-cyan-400" /> Compare any two — including captured plan vs LLM
              </div>
              <div className="flex items-center gap-2">
                <select value={compareFrom} onChange={(e) => setCompareFrom(e.target.value)}
                  className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 outline-none focus:border-accent/50">
                  <option value="">from…</option>
                  {allVersions.map((v) => <option key={v.ref} value={v.ref}>{v.label}</option>)}
                </select>
                <span className="text-slate-600">→</span>
                <select value={compareTo} onChange={(e) => setCompareTo(e.target.value)}
                  className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 outline-none focus:border-accent/50">
                  <option value="">to…</option>
                  {allVersions.map((v) => <option key={v.ref} value={v.ref}>{v.label}</option>)}
                </select>
                <button onClick={runCompare} disabled={comparing || !compareFrom || !compareTo || compareFrom === compareTo}
                  className="px-2.5 py-1.5 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-500/30 text-cyan-200 text-[11px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed shrink-0">
                  {comparing ? <Loader2 size={12} className="animate-spin" /> : "Diff"}
                </button>
              </div>
            </>
          )}

          {compareData && (
            <div className="space-y-2 pt-1">
              <div className="flex items-center gap-2 text-[10px]">
                <span className="text-slate-500">{compareData.from.label}</span>
                <ArrowRight size={10} className="text-slate-600" />
                <span className="text-slate-500">{compareData.to.label}</span>
                {compareData.cross_source && (
                  <span className="px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/25 uppercase tracking-wide">cross-source</span>
                )}
                <span className="text-slate-500 ml-auto">{compareData.changed_count} change{compareData.changed_count !== 1 && "s"}</span>
              </div>
              <div className="rounded-lg border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                {compareData.column_diffs.filter((d) => d.status !== "unchanged").length === 0 && (
                  <div className="px-2.5 py-2 text-[10px] text-slate-600">No differences between these versions.</div>
                )}
                {compareData.column_diffs.filter((d) => d.status !== "unchanged").map((d) => (
                  <div key={d.column} className="px-2.5 py-1.5 bg-surface-100/40">
                    <div className="flex items-center gap-2">
                      <span className={`text-[9px] px-1.5 py-0.5 rounded uppercase ${
                        d.status === "added" ? "bg-emerald-500/15 text-emerald-300"
                        : d.status === "removed" ? "bg-rose-500/15 text-rose-300"
                        : "bg-amber-500/15 text-amber-300"}`}>{d.status}</span>
                      <span className="font-mono text-[11px] text-slate-200">{d.column}</span>
                    </div>
                    {d.status === "changed" && (
                      <div className="mt-1 space-y-0.5 font-mono text-[10px]">
                        <div className="text-rose-300/80 break-words">- {d.from?.expression || d.from?.transformation || "(none)"}</div>
                        <div className="text-emerald-300/80 break-words">+ {d.to?.expression || d.to?.transformation || "(none)"}</div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
