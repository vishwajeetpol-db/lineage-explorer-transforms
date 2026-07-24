import { useCallback, useEffect, useMemo, useState } from "react";
import { Sparkles, Play, Loader2, RefreshCw, GitCompare, AlertTriangle, History } from "lucide-react";
import {
  api,
  type AnalyzeProducerResponse,
  type AnalyzeProducerColumn,
  type AnalysisCompare,
} from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";
import { NoTable, SectionTitle, parseFqn } from "./panelShared";

const ENTITY_TYPES = ["NOTEBOOK", "JOB", "PIPELINE", "QUERY"];

function ColumnCard({ c }: { c: AnalyzeProducerColumn }) {
  return (
    <div className="px-3 py-2.5 bg-surface-100/40 space-y-1">
      <div className="flex items-center gap-2">
        <span className="font-mono text-[11px] text-violet-200">{c.target_column || c.column || "?"}</span>
        {c.category && <span className="text-[9px] px-1.5 py-0.5 rounded bg-surface-200 text-slate-400">{c.category}</span>}
        {c.confidence != null && <span className="text-[9px] text-slate-500 ml-auto">conf {String(c.confidence)}</span>}
      </div>
      {(c.expression || c.transformation) && (
        <div className="font-mono text-[10px] text-slate-400 break-words">{c.expression || c.transformation}</div>
      )}
      {c.source_columns && c.source_columns.length > 0 && (
        <div className="text-[10px] text-slate-600">from: {c.source_columns.join(", ")}</div>
      )}
    </div>
  );
}

export default function LLMTransformPanel({ table }: { table: string | null }) {
  const nodes = useLineageStore((s) => s.nodes);
  const entityNodes = useMemo(
    () => nodes.filter((n): n is Extract<typeof n, { node_type: "entity" }> => n.node_type === "entity"),
    [nodes],
  );

  const [entityType, setEntityType] = useState("PIPELINE");
  const [entityId, setEntityId] = useState("");
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");
  const [result, setResult] = useState<AnalyzeProducerResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Version compare state
  const [compareFrom, setCompareFrom] = useState<number | "">("");
  const [compareTo, setCompareTo] = useState<number | "">("");
  const [compareData, setCompareData] = useState<AnalysisCompare | null>(null);
  const [comparing, setComparing] = useState(false);

  const parts = parseFqn(table);

  // Load model list once.
  useEffect(() => {
    api.getAnalyzeModels()
      .then((r) => { setModels(r.models); setModel((m) => m || r.default); })
      .catch(() => { setModels(["databricks-claude-sonnet-4-6"]); setModel((m) => m || "databricks-claude-sonnet-4-6"); });
  }, []);

  // Reset when table changes.
  useEffect(() => { setResult(null); setError(null); setCompareData(null); setEntityId(""); }, [table]);

  // Auto-load the latest stored version whenever a producer is chosen (no LLM cost).
  const loadStored = useCallback(async (et: string, eid: string) => {
    if (!parts || !eid.trim()) return;
    setLoading(true); setError(null); setCompareData(null);
    try {
      const r = await api.analyzeProducer({
        entity_type: et,
        entity_id: eid.trim(),
        target_table: `${parts.catalog}.${parts.schema}.${parts.table}`,
        force_rerun: false,
      });
      setResult(r);
    } catch (e: any) {
      setError(e.message || "Failed to load analysis");
    } finally { setLoading(false); }
  }, [table]); // eslint-disable-line react-hooks/exhaustive-deps

  const pickProducer = (et: string, eid: string) => {
    setEntityType(et); setEntityId(eid);
    loadStored(et, eid);
  };

  const runAnalysis = async () => {
    if (!parts || !entityId.trim()) return;
    setLoading(true); setError(null); setCompareData(null);
    try {
      const r = await api.analyzeProducer({
        entity_type: entityType,
        entity_id: entityId.trim(),
        target_table: `${parts.catalog}.${parts.schema}.${parts.table}`,
        force_rerun: true,
        model: model || undefined,
      });
      setResult(r);
    } catch (e: any) {
      setError(e.message || "Analysis failed");
    } finally { setLoading(false); }
  };

  const runCompare = async () => {
    if (!compareFrom || !compareTo || compareFrom === compareTo || !parts) return;
    setComparing(true);
    try {
      const tbl = `${parts.catalog}.${parts.schema}.${parts.table}`;
      const c = await api.compareAnalysisVersions(entityType, entityId.trim(), tbl, Number(compareFrom), Number(compareTo));
      setCompareData(c);
    } catch (e: any) {
      setError(e.message || "Compare failed");
    } finally { setComparing(false); }
  };

  if (!table) return <NoTable />;

  const hasStored = result && result.version != null;
  const isStale = !!result?.stale;

  return (
    <div className="space-y-4">
      {/* Intro */}
      <div className="rounded-xl bg-violet-500/[0.06] border border-violet-500/20 px-4 py-3">
        <div className="flex items-center gap-2 text-[12px] text-violet-200">
          <Sparkles size={14} className="text-violet-400" />
          LLM-inferred column transformations
        </div>
        <p className="text-[11px] text-slate-500 mt-1">
          Analyses are versioned. Loading shows the last stored version (no LLM cost); Re-analyze
          runs the model and saves a new version.
        </p>
      </div>

      {/* Producer picker from the graph */}
      {entityNodes.length > 0 && (
        <div>
          <SectionTitle>Producers in this lineage</SectionTitle>
          <div className="flex flex-wrap gap-1.5">
            {entityNodes.map((e) => (
              <button
                key={e.id}
                onClick={() => pickProducer(e.entity_type || "PIPELINE", e.entity_id)}
                className={`text-[11px] px-2 py-1 rounded-lg border transition-colors ${
                  entityId === e.entity_id
                    ? "bg-violet-500/20 border-violet-500/40 text-violet-200"
                    : "bg-surface-100/60 border-white/[0.06] text-slate-300 hover:border-violet-500/30"
                }`}
              >
                {e.display_name || `${e.entity_type} ${e.entity_id}`}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Manual entry + model dropdown */}
      <div className="space-y-2">
        <SectionTitle>Producer &amp; model</SectionTitle>
        <div className="flex gap-2">
          <select
            value={entityType}
            onChange={(e) => setEntityType(e.target.value)}
            className="px-2 py-2 bg-surface-100 border border-white/[0.08] rounded-lg text-[12px] text-slate-200 focus:border-accent/50 outline-none"
          >
            {ENTITY_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>
          <input
            value={entityId}
            onChange={(e) => setEntityId(e.target.value)}
            onBlur={() => entityId.trim() && loadStored(entityType, entityId)}
            placeholder="entity id"
            className="flex-1 min-w-0 px-3 py-2 bg-surface-100 border border-white/[0.08] rounded-lg text-[12px] text-slate-200 font-mono placeholder:text-slate-600 focus:border-accent/50 outline-none"
          />
        </div>
        {/* Model dropdown — lets you pick a cheaper/pricier endpoint per run */}
        <div className="flex items-center gap-2">
          <span className="text-[10px] uppercase tracking-wider text-slate-500 shrink-0">Model</span>
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            className="flex-1 min-w-0 px-2 py-2 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 font-mono focus:border-accent/50 outline-none"
          >
            {models.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </div>
        <button
          onClick={runAnalysis}
          disabled={loading || !entityId.trim()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200 text-[12px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {loading ? <Loader2 size={13} className="animate-spin" /> : hasStored ? <RefreshCw size={13} /> : <Play size={13} />}
          {loading ? "Working…" : hasStored ? "Re-analyze (new version)" : "Analyze producer"}
        </button>
      </div>

      {error && (
        <div className="text-[11px] text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-2 break-words">
          {error}
        </div>
      )}

      {result && (
        <div className="space-y-3">
          {/* Version / source status bar */}
          <div className="flex flex-wrap items-center gap-2 text-[10px]">
            <span className={`px-1.5 py-0.5 rounded ${
              result.source === "llm" ? "bg-violet-500/15 text-violet-300"
              : result.source === "stored" ? "bg-sky-500/15 text-sky-300"
              : "bg-surface-200 text-slate-400"
            }`}>{result.source}</span>
            {result.version != null && (
              <span className="flex items-center gap-1 text-slate-400"><History size={10} /> v{result.version}</span>
            )}
            {result.llm_model && <span className="text-slate-500 font-mono">{result.llm_model}</span>}
            {result.analyzed_at && <span className="text-slate-600">{result.analyzed_at.slice(0, 16).replace("T", " ")}</span>}
            {result.detail && <span className="text-amber-400">{result.detail}</span>}
          </div>

          {/* Stale banner — drives the "source changed → re-analyze" cue */}
          {isStale && (
            <div className="flex items-center gap-2 text-[11px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-3 py-2">
              <AlertTriangle size={13} className="shrink-0" />
              Producer source code has changed since v{result.version}. Re-analyze to capture it.
            </div>
          )}

          {/* Columns */}
          {result.columns.length > 0 && (
            <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
              {result.columns.map((c, i) => <ColumnCard key={i} c={c} />)}
            </div>
          )}

          {/* Version compare */}
          {result.versions.length > 1 && (
            <div className="rounded-xl bg-surface-100/40 border border-white/[0.06] px-3 py-3 space-y-2">
              <div className="flex items-center gap-1.5 text-[11px] text-slate-300">
                <GitCompare size={13} className="text-cyan-400" /> Compare versions
              </div>
              <div className="flex items-center gap-2">
                <select value={compareFrom} onChange={(e) => setCompareFrom(e.target.value ? Number(e.target.value) : "")}
                  className="flex-1 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 outline-none focus:border-accent/50">
                  <option value="">from…</option>
                  {result.versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.analyzed_at?.slice(0,10)}</option>)}
                </select>
                <span className="text-slate-600">→</span>
                <select value={compareTo} onChange={(e) => setCompareTo(e.target.value ? Number(e.target.value) : "")}
                  className="flex-1 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 outline-none focus:border-accent/50">
                  <option value="">to…</option>
                  {result.versions.map((v) => <option key={v.version} value={v.version}>v{v.version} · {v.analyzed_at?.slice(0,10)}</option>)}
                </select>
                <button onClick={runCompare} disabled={comparing || !compareFrom || !compareTo || compareFrom === compareTo}
                  className="px-2.5 py-1.5 rounded-lg bg-cyan-500/15 hover:bg-cyan-500/25 border border-cyan-500/30 text-cyan-200 text-[11px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed shrink-0">
                  {comparing ? <Loader2 size={12} className="animate-spin" /> : "Diff"}
                </button>
              </div>

              {compareData && (
                <div className="space-y-2 pt-1">
                  <div className="text-[10px] text-slate-500">
                    {compareData.changed_count} column change{compareData.changed_count !== 1 && "s"} ·
                    {compareData.source_changed ? " source code changed" : " source code identical"}
                  </div>
                  <div className="rounded-lg border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                    {compareData.column_diffs.filter((d) => d.status !== "unchanged").map((d) => (
                      <div key={d.column} className="px-2.5 py-1.5 bg-surface-100/40">
                        <div className="flex items-center gap-2">
                          <span className={`text-[9px] px-1.5 py-0.5 rounded uppercase ${
                            d.status === "added" ? "bg-emerald-500/15 text-emerald-300"
                            : d.status === "removed" ? "bg-rose-500/15 text-rose-300"
                            : "bg-amber-500/15 text-amber-300"
                          }`}>{d.status}</span>
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
                    {compareData.column_diffs.every((d) => d.status === "unchanged") && (
                      <div className="px-2.5 py-2 text-[10px] text-slate-600">No column-level changes between these versions.</div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
