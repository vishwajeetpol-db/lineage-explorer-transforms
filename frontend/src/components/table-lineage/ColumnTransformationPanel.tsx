import { useCallback, useEffect, useMemo, useState } from "react";
import {
  GitFork, Play, Loader2, RefreshCw, GitCompare, AlertTriangle, History,
  Cpu, Sparkles, Layers, ArrowRight, CheckCircle2, ShieldAlert, Copy, Check, Columns3,
  Search, Info, Wand2,
} from "lucide-react";
import {
  api,
  type ColumnTransformResult,
  type AnalyzeProducerColumn,
  type TransformVersion,
  type TransformVersionDetail,
  type CrossSourceCompare,
  type ProducerCompare,
  type DeepAnalyzeStep,
} from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";
import { NoTable, parseFqn } from "./panelShared";
import ColumnOverviewModal from "./ColumnOverviewModal";

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

// Shown when the LLM path failed because the app's service principal can't read
// the producer's source code — a common, user-fixable permission gap. Gives the
// exact principal + resources and a copyable grant command.
function AccessDeniedNotice({ data }: { data: ColumnTransformResult }) {
  const [copied, setCopied] = useState(false);
  const sp = data.app_service_principal;
  const paths = data.denied_paths || [];
  const grantCmd = sp
    ? `# Grant the app's service principal read access to the producer, then re-analyze:\n`
      + `databricks pipelines ... # or: grant CAN_VIEW on the producing pipeline/notebook\n`
      + paths.map((p) => `# denied: ${p}`).join("\n")
    : "";

  const copy = () => {
    if (!grantCmd) return;
    navigator.clipboard?.writeText(grantCmd).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.07] px-3.5 py-3 space-y-2">
      <div className="flex items-center gap-2">
        <ShieldAlert size={15} className="text-amber-400 shrink-0" />
        <span className="text-[12px] font-semibold text-amber-900 dark:text-amber-100">Access to producer code required</span>
      </div>
      <p className="text-[11px] text-amber-200/90 leading-relaxed">
        The producer&apos;s source code exists, but this app can&apos;t read it — so the LLM
        has nothing to analyze. Grant the app&apos;s service principal read access to the
        producing entity and its source, then click <span className="font-medium">Re-analyze</span>.
      </p>
      {sp && (
        <div className="text-[10px] text-amber-200/80">
          <span className="text-amber-300/70">App service principal:</span>{" "}
          <span className="font-mono select-all">{sp}</span>
        </div>
      )}
      {paths.length > 0 && (
        <div className="rounded-lg bg-black/25 border border-amber-500/15 px-2.5 py-1.5 space-y-1">
          <div className="text-[9px] uppercase tracking-wider text-amber-300/60">Denied</div>
          {paths.slice(0, 5).map((p, i) => (
            <div key={i} className="font-mono text-[10px] text-amber-900 dark:text-amber-100/90 break-all">{p}</div>
          ))}
        </div>
      )}
      <div className="text-[10px] text-amber-200/70 leading-relaxed">
        Grant <span className="font-mono text-amber-900 dark:text-amber-100">CAN_VIEW</span> /{" "}
        <span className="font-mono text-amber-900 dark:text-amber-100">CAN_READ</span> on the producing
        pipeline/notebook and the workspace files above (in the Databricks UI:
        the entity&apos;s <span className="italic">Permissions</span> dialog).
      </div>
      {grantCmd && (
        <button onClick={copy}
          className="flex items-center gap-1.5 text-[10px] px-2 py-1 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-amber-900 dark:text-amber-100 transition-colors">
          {copied ? <Check size={11} /> : <Copy size={11} />}
          {copied ? "Copied" : "Copy details"}
        </button>
      )}
    </div>
  );
}

// Per-column matrix comparing how each producer computes each output column.
// Rows = target columns; columns = producers; divergent rows highlighted.
function ProducerCompareMatrix({ cmp, onRefresh }: { cmp: ProducerCompare; onRefresh: () => void }) {
  const shortLabel = (p: ProducerCompare["producers"][number]) =>
    p.label && !p.label.startsWith(p.entity_type) ? p.label : `${p.entity_type} ${p.entity_id.slice(0, 8)}`;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[10px] text-slate-400">
        <span>
          <b className="text-amber-300">{cmp.divergent_count}</b> of {cmp.column_count} columns differ across producers
        </span>
        <button onClick={onRefresh} title="Re-resolve all producers"
          className="ml-auto flex items-center gap-1 text-slate-500 hover:text-accent-light transition-colors">
          <RefreshCw size={11} /> Refresh
        </button>
      </div>

      {/* Producer legend + any that couldn't be read */}
      {cmp.producers.some((p) => p.source === "unavailable") && (
        <div className="text-[10px] text-amber-200/80 bg-amber-500/10 border border-amber-500/25 rounded px-2 py-1">
          Some producers couldn't be read (e.g. access denied) — their column cells show "—".
        </div>
      )}

      <div className="overflow-x-auto rounded-lg border border-white/[0.08]">
        <table className="w-full border-collapse text-[10.5px]">
          <thead>
            <tr className="bg-surface-200/60">
              <th className="text-left font-medium text-slate-400 px-2 py-1.5 sticky left-0 bg-surface-200/60 border-r border-white/[0.06]">
                Column
              </th>
              {cmp.producers.map((p) => (
                <th key={p.key} className="text-left font-medium text-slate-300 px-2 py-1.5 min-w-[150px] border-r border-white/[0.04] last:border-0">
                  <span className="truncate block max-w-[180px]" title={`${p.entity_type} ${p.entity_id}`}>{shortLabel(p)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {cmp.columns.map((row) => (
              <tr key={row.column} className={`border-t border-white/[0.05] ${row.divergent ? "bg-amber-500/[0.06]" : ""}`}>
                <td className="align-top px-2 py-1.5 sticky left-0 border-r border-white/[0.06] bg-surface-100/80">
                  <span className="font-mono text-slate-200 flex items-center gap-1">
                    {row.divergent && <AlertTriangle size={10} className="text-amber-400 shrink-0" />}
                    {row.column}
                  </span>
                </td>
                {row.cells.map((cell) => (
                  <td key={cell.producer} className="align-top px-2 py-1.5 border-r border-white/[0.04] last:border-0">
                    {cell.present ? (
                      <div className="space-y-0.5">
                        <div className="font-mono text-slate-300 break-words">{cell.expression || "—"}</div>
                        {cell.source_columns.length > 0 && (
                          <div className="text-[9px] text-slate-500 font-mono">← {cell.source_columns.join(", ")}</div>
                        )}
                        {cell.category && (
                          <span className="text-[8px] uppercase tracking-wide text-slate-500">{cell.category}</span>
                        )}
                      </div>
                    ) : (
                      <span className="text-slate-600">—</span>
                    )}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Live commentary log for the deep framework analysis. */
function DeepLog({ steps, running }: { steps: DeepAnalyzeStep[]; running: boolean }) {
  const icon = (s: DeepAnalyzeStep["status"]) =>
    s === "ok" ? <CheckCircle2 size={11} className="text-emerald-400 shrink-0 mt-px" />
      : s === "warn" ? <AlertTriangle size={11} className="text-amber-400 shrink-0 mt-px" />
        : s === "error" ? <AlertTriangle size={11} className="text-rose-400 shrink-0 mt-px" />
          : <Loader2 size={11} className="text-violet-400 shrink-0 mt-px animate-spin" />;
  const color = (s: DeepAnalyzeStep["status"]) =>
    s === "ok" ? "text-slate-300" : s === "warn" ? "text-amber-200/90" : s === "error" ? "text-rose-300" : "text-slate-400";
  return (
    <div className="rounded-lg border border-white/[0.08] bg-black/30 p-2.5 max-h-56 overflow-y-auto space-y-1">
      {steps.map((s, i) => (
        <div key={i} className="flex items-start gap-1.5 text-[10px] font-mono leading-snug">
          {icon(s.status)}<span className={color(s.status)}>{s.message}</span>
        </div>
      ))}
      {running && (
        <div className="flex items-center gap-1.5 text-[10px] text-slate-500 font-mono">
          <Loader2 size={10} className="animate-spin" /> working…
        </div>
      )}
    </div>
  );
}

export default function ColumnTransformationPanel({ table }: { table: string | null }) {
  const nodes = useLineageStore((s) => s.nodes);
  const edges = useLineageStore((s) => s.edges);
  const entityNodes = useMemo(
    () => nodes.filter((n): n is Extract<typeof n, { node_type: "entity" }> => n.node_type === "entity"),
    [nodes],
  );
  // Producers = entity nodes with an edge INTO the focus table (i.e. they write it).
  const producerNodes = useMemo(() => {
    if (!table) return [];
    const producerIds = new Set(
      edges.filter((e) => e.target === table).map((e) => e.source),
    );
    return entityNodes.filter((n) => producerIds.has(n.id));
  }, [entityNodes, edges, table]);

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
  // A specific version being viewed from the history list (null = showing the resolved/current result).
  const [viewingVersion, setViewingVersion] = useState<TransformVersionDetail | null>(null);
  const [viewLoading, setViewLoading] = useState<string | null>(null);

  // Multi-producer comparison (only when the table has 2+ producers).
  const [producerCmp, setProducerCmp] = useState<ProducerCompare | null>(null);
  const [pcLoading, setPcLoading] = useState(false);

  // Panel structure: which tab is active, and a filter for the columns list.
  type Tab = "columns" | "analyze" | "history" | "producers";
  const [activeTab, setActiveTab] = useState<Tab>("columns");
  const [columnFilter, setColumnFilter] = useState("");
  const [showLegend, setShowLegend] = useState(false);
  const [overviewOpen, setOverviewOpen] = useState(false);
  // Deep framework fallback (metadata-driven producers): live commentary log.
  const [deepRunning, setDeepRunning] = useState(false);
  const [deepLog, setDeepLog] = useState<DeepAnalyzeStep[]>([]);

  const parts = parseFqn(table);

  const viewVersion = async (ref: string) => {
    if (!parts) return;
    setViewLoading(ref); setError(null);
    try {
      const v = await api.getTransformationVersion({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table, ref,
        entity_type: entityId ? entityType : undefined, entity_id: entityId || undefined,
      });
      setViewingVersion(v);
    } catch (e: any) {
      setError(e.message || "Failed to load version");
    } finally { setViewLoading(null); }
  };

  useEffect(() => {
    api.getAnalyzeModels()
      .then((r) => { setModels(r.models); setModel((m) => m || r.default); })
      .catch(() => { setModels(["databricks-claude-sonnet-4-6"]); setModel((m) => m || "databricks-claude-sonnet-4-6"); });
  }, []);

  // Resolve every producer of this table and build the comparison matrix.
  const runProducerCompare = useCallback(async (force = false) => {
    if (!parts || producerNodes.length < 2) return;
    setPcLoading(true); setError(null);
    try {
      const r = await api.compareProducers({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        producers: producerNodes.map((n) => ({ entity_type: n.entity_type, entity_id: n.entity_id })),
        force_rerun: force,
      });
      setProducerCmp(r);
    } catch (e: any) {
      setError(e.message || "Failed to compare producers");
    } finally { setPcLoading(false); }
  }, [parts, producerNodes]); // eslint-disable-line react-hooks/exhaustive-deps

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
    setLoading(true); setError(null); setCompareData(null); setViewingVersion(null);
    try {
      const r = await api.resolveColumnTransformations({
        catalog: parts.catalog, schema_name: parts.schema, table: parts.table,
        entity_type: opts?.et ?? (entityId ? entityType : undefined),
        entity_id: opts?.eid ?? (entityId || undefined),
        force_rerun: opts?.force || false,
        model: opts?.force ? (model || undefined) : undefined,
      });
      setData(r);
      // If the resolver surfaced an existing analysis for one of this table's
      // producers (no producer was explicitly picked), adopt it so the Analyze
      // tab pre-selects that producer and Re-analyze / Deep analysis target it.
      if (r.entity_type && r.entity_id && !opts?.eid && !entityId) {
        setEntityType(r.entity_type);
        setEntityId(r.entity_id);
      }
      loadVersions(
        opts?.et ?? (r.entity_type || (entityId ? entityType : undefined)),
        opts?.eid ?? (r.entity_id || entityId || undefined),
      );
    } catch (e: any) {
      setError(e.message || "Failed to resolve column transformations");
    } finally { setLoading(false); }
  }, [table, entityType, entityId, model, loadVersions]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { setData(null); setError(null); setEntityId(""); setCompareData(null); setAllVersions([]); setViewingVersion(null); setProducerCmp(null); setActiveTab("columns"); setColumnFilter(""); setShowLegend(false); setOverviewOpen(false); setDeepLog([]); setDeepRunning(false); }, [table]);
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

  // Deep framework fallback: stream the agentic analysis, appending each step to
  // the live commentary log; on a derived result, refresh the panel's columns.
  const runDeep = async () => {
    if (!parts) return;
    const et = (data?.entity_type || entityType || "").toUpperCase();
    const eid = data?.entity_id || entityId;
    if (!et || !eid) { setError("Pick a producer first, then run deep analysis."); return; }
    setDeepRunning(true); setDeepLog([]); setError(null);
    try {
      await api.deepAnalyzeColumnTransformations(
        { catalog: parts.catalog, schema_name: parts.schema, table: parts.table, entity_type: et, entity_id: eid, model: model || undefined },
        (ev) => {
          if (ev.type === "step") {
            setDeepLog((l) => [...l, ev]);
          } else if (ev.type === "error") {
            setDeepLog((l) => [...l, { type: "step", step: "error", status: "error", message: ev.message }]);
          } else if (ev.type === "result") {
            setDeepLog((l) => [...l, {
              type: "step", step: "done",
              status: ev.derived ? "ok" : "warn",
              message: ev.derived
                ? `Done — derived ${ev.columns.length} column(s)${ev.version ? ` (v${ev.version})` : ""}. See the Columns tab.`
                : (ev.detail || "No columns could be derived."),
            }]);
            if (ev.derived) resolve({ et, eid }); // pull in the newly-saved version
          }
        },
      );
    } catch (e: any) {
      setError(e?.message || "Deep analysis failed");
    } finally { setDeepRunning(false); }
  };

  if (!table) return <NoTable />;

  const src = data?.source || "none";
  const meta = SOURCE_META[src] || SOURCE_META.none;
  const SrcIcon = meta.icon;
  const isLLM = src === "stored" || src === "llm";
  const canReanalyze = isLLM || (data?.source === "none" && !!entityId);
  const hasProducers = producerNodes.length >= 2;

  // "unavailable" is a catch-all; surface the ACTUAL reason (usually the producer
  // source couldn't be read) instead of the misleading "LLM unavailable".
  const unavailReason = src === "unavailable"
    ? (data?.reason_code === "access_denied" ? "Source access denied"
      : data?.reason_code === "entity_missing" ? "Producer not found"
      : data?.reason_code === "no_source" ? "No readable source code"
      : data?.reason_code === "no_columns" ? "No column logic in source"
      : data?.reason_code === "llm_not_configured" ? "LLM not configured"
      : data?.reason_code === "llm_error" ? "LLM analysis error"
      : "LLM unavailable")
    : null;
  const headerLabel = unavailReason || data?.source_label || meta.label;
  // Which producer is this lineage from? Prefer the graph node's friendly name,
  // falling back to the backend's plain label / TYPE+id. Shown as a chip so the
  // user always knows whose logic is on screen (esp. when it was auto-surfaced
  // across producers on open).
  const producerName = isLLM && data && data.entity_id
    ? (producerNodes.find((n) => n.entity_id === data.entity_id)?.display_name
        || data.producer_label
        || `${data.entity_type || ""} ${data.entity_id}`.trim())
    : null;
  // Show the reason text when unavailable for a reason other than access-denied
  // (access-denied has its own richer notice).
  const showUnavailDetail = src === "unavailable" && data?.reason_code !== "access_denied" && !!data?.detail;

  // Columns filtered by the search box (target column or a source column).
  const q = columnFilter.trim().toLowerCase();
  const filteredColumns = (data?.columns ?? []).filter((c) => {
    if (!q) return true;
    const name = (c.target_column || c.column || "").toLowerCase();
    const srcs = (c.source_columns || []).join(" ").toLowerCase();
    return name.includes(q) || srcs.includes(q);
  });

  const tabs: { key: Tab; label: string; count?: number }[] = [
    { key: "columns", label: "Columns", count: data?.columns.length || undefined },
    { key: "analyze", label: "Analyze" },
    { key: "history", label: "History", count: allVersions.length || undefined },
    ...(hasProducers ? [{ key: "producers" as Tab, label: "Producers", count: producerNodes.length }] : []),
  ];
  // Guard: if the active tab vanished (e.g. producers on a new table), fall back.
  const effectiveTab: Tab = activeTab === "producers" && !hasProducers ? "columns" : activeTab;

  const goTab = (t: Tab) => {
    setActiveTab(t);
    if (t === "producers" && !producerCmp && !pcLoading) runProducerCompare(false);
  };

  return (
    <div className="space-y-3">
      {/* Compact source-of-truth header (persistent across tabs). */}
      {data && (
        <div className={`rounded-xl border px-3 py-2 ${
          src === "plan_capture" ? "bg-emerald-500/[0.07] border-emerald-500/25"
          : src === "cdc_spec" ? "bg-sky-500/[0.07] border-sky-500/25"
          : isLLM ? "bg-violet-500/[0.07] border-violet-500/25"
          : "bg-surface-100/50 border-white/[0.08]"
        }`}>
          <div className="flex items-center gap-2">
            <SrcIcon size={14} className={`${meta.color} shrink-0`} />
            <span className="text-[12px] font-semibold text-slate-100 truncate">{headerLabel}</span>
            {data.version != null && <span className="text-[10px] text-slate-500 shrink-0">v{data.version}</span>}
            {data.columns.length > 0 && <span className="text-[10px] text-slate-500 shrink-0">· {data.columns.length} cols</span>}
            {src === "plan_capture" && <CheckCircle2 size={12} className="text-emerald-400 shrink-0" />}
            <button onClick={() => setShowLegend((v) => !v)} title="How lineage is resolved"
              className="ml-auto shrink-0 text-slate-500 hover:text-accent-light transition-colors">
              <Info size={13} />
            </button>
          </div>
          {producerName && (
            <div className="flex items-center gap-1.5 mt-1.5 text-[10px] text-slate-400">
              <GitFork size={10} className="text-violet-400 shrink-0 rotate-90" />
              <span className="shrink-0 text-slate-500">Producer:</span>
              <span className="font-mono text-violet-300 truncate" title={`${data?.entity_type || ""} ${data?.entity_id || ""}`}>{producerName}</span>
              {hasProducers && (
                <span className="shrink-0 text-slate-600">· latest of {producerNodes.length} — compare in Producers tab</span>
              )}
            </div>
          )}
          {data.stale && (
            <div className="flex items-center gap-1.5 text-[10px] text-amber-200 bg-amber-500/10 border border-amber-500/25 rounded-lg px-2 py-1 mt-2">
              <AlertTriangle size={11} /> Producer source changed since this analysis — re-analyze to refresh.
            </div>
          )}
          {/* Precedence legend (on demand) */}
          {showLegend && (
            <div className="mt-2 pt-2 border-t border-white/[0.06] space-y-1.5">
              <p className="text-[10px] text-slate-500">Each column is resolved best-source-first:</p>
              <div className="flex items-center gap-1 flex-wrap text-[9px]">
                <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/25">Captured plan</span>
                <ArrowRight size={9} className="text-slate-600" />
                <span className="px-1.5 py-0.5 rounded bg-sky-500/15 text-sky-300 border border-sky-500/25">CDC spec</span>
                <ArrowRight size={9} className="text-slate-600" />
                <span className="px-1.5 py-0.5 rounded bg-violet-500/15 text-violet-300 border border-violet-500/25">Stored LLM</span>
                <ArrowRight size={9} className="text-slate-600" />
                <span className="px-1.5 py-0.5 rounded bg-fuchsia-500/15 text-fuchsia-300 border border-fuchsia-500/25">Fresh LLM</span>
              </div>
              {meta.blurb && <p className="text-[10px] text-slate-500">{meta.blurb}</p>}
              {(data.captured_at || data.analyzed_at) && (
                <p className="text-[10px] text-slate-600">{(data.captured_at || data.analyzed_at || "").slice(0, 19).replace("T", " ")}</p>
              )}
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div role="tablist" className="flex items-center gap-1 border-b border-white/[0.06]">
        {tabs.map((t) => (
          <button key={t.key} role="tab" aria-selected={effectiveTab === t.key} onClick={() => goTab(t.key)}
            className={`flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium border-b-2 -mb-px transition-colors ${
              effectiveTab === t.key
                ? "border-violet-400 text-slate-100"
                : "border-transparent text-slate-500 hover:text-slate-300"
            }`}>
            {t.label}
            {t.count != null && (
              <span className={`text-[9px] px-1 rounded ${effectiveTab === t.key ? "bg-violet-500/20 text-violet-200" : "bg-surface-200 text-slate-500"}`}>{t.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* Top-level error (any tab) */}
      {error && (
        <div className="text-[11px] text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-3 py-2 break-words">{error}</div>
      )}

      {/* ---- Columns tab ---- */}
      {effectiveTab === "columns" && (
        <div className="space-y-2">
          {loading && !data && (
            <div className="flex items-center justify-center gap-2 py-10 text-slate-500">
              <Loader2 size={18} className="animate-spin text-accent" /> <span className="text-[12px]">Resolving…</span>
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

          {data && data.columns.length > 0 && (
            <>
              {/* Glowing AI overview trigger — opens the wide LLM overview modal. */}
              <button onClick={() => setOverviewOpen(true)} title="Plain-English AI overview of every column"
                className="ai-glow w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-gradient-to-r from-violet-500/25 to-fuchsia-500/20 hover:from-violet-500/35 hover:to-fuchsia-500/30 border border-violet-400/40 text-violet-900 dark:text-violet-100 text-[12px] font-semibold transition-colors">
                <Sparkles size={13} className="text-violet-600 dark:text-violet-300" /> AI overview of all columns
              </button>
              {data.columns.length > 6 && (
                <div className="flex items-center gap-2 px-2.5 py-1.5 bg-surface-100/60 border border-white/[0.08] rounded-lg focus-within:border-accent/40">
                  <Search size={12} className="text-slate-500 shrink-0" />
                  <input value={columnFilter} onChange={(e) => setColumnFilter(e.target.value)} placeholder="Filter columns…"
                    className="bg-transparent text-[11px] text-slate-200 placeholder:text-slate-600 outline-none flex-1 min-w-0" />
                </div>
              )}
              <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                {filteredColumns.map((c, i) => <ColumnCard key={i} c={c} />)}
                {filteredColumns.length === 0 && (
                  <div className="px-3 py-3 text-[10px] text-slate-600">No columns match “{columnFilter}”.</div>
                )}
              </div>
            </>
          )}

          {data && data.columns.length === 0 && !loading && data.source !== "cdc_spec" && (
            data.reason_code === "access_denied"
              ? <AccessDeniedNotice data={data} />
              : (
                <div className="text-[11px] text-slate-500 py-2 space-y-2">
                  <p>{data.detail || "No column transformations resolved yet."}</p>
                  <button onClick={() => setActiveTab("analyze")}
                    className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200 font-medium transition-colors">
                    <Sparkles size={12} /> Run LLM analysis
                  </button>
                </div>
              )
          )}
        </div>
      )}

      {/* ---- Analyze tab ---- */}
      {effectiveTab === "analyze" && (
        <div className="space-y-2.5">
          <p className="text-[11px] text-slate-500 flex items-center gap-1.5">
            <Sparkles size={12} className="text-violet-400 shrink-0" />
            Infer column lineage from a producer&apos;s source code when no captured plan exists — or refresh a stale one.
          </p>
          {data?.reason_code === "access_denied" && <AccessDeniedNotice data={data} />}
          {showUnavailDetail && data?.reason_code !== "no_columns" && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/[0.07] px-3 py-2 text-[11px] text-amber-200/90">
              <AlertTriangle size={13} className="text-amber-400 shrink-0 mt-0.5" />
              <span>{data?.detail}</span>
            </div>
          )}

          {/* Deep framework fallback — for metadata-driven producers whose column
              logic lives in config tables/params rather than the code. */}
          {(data?.reason_code === "no_columns" || deepRunning || deepLog.length > 0) && (
            <div className="rounded-xl border border-fuchsia-500/30 bg-fuchsia-500/[0.06] px-3 py-3 space-y-2.5">
              <div className="flex items-center gap-2">
                <Wand2 size={14} className="text-fuchsia-400 shrink-0" />
                <span className="text-[12px] font-semibold text-slate-100">Deep framework analysis</span>
              </div>
              <p className="text-[10px] text-slate-400 leading-relaxed">
                This producer looks like a metadata-driven framework — no column logic in the code itself. Deep analysis
                detects its config tables &amp; parameters, queries them, and derives the columns, narrating each step.
              </p>
              {!deepRunning && (
                <button onClick={runDeep}
                  className="ai-glow w-full flex items-center justify-center gap-2 px-3 py-2 rounded-lg bg-gradient-to-r from-fuchsia-500/25 to-violet-500/20 hover:from-fuchsia-500/35 hover:to-violet-500/30 border border-fuchsia-400/40 text-fuchsia-900 dark:text-fuchsia-100 text-[12px] font-semibold transition-colors">
                  <Wand2 size={13} className="text-fuchsia-600 dark:text-fuchsia-200" /> {deepLog.length ? "Re-run deep analysis" : "Run deep framework analysis"}
                </button>
              )}
              {(deepRunning || deepLog.length > 0) && <DeepLog steps={deepLog} running={deepRunning} />}
            </div>
          )}

          {producerNodes.length > 0 && (
            <div className="space-y-1">
              <div className="text-[10px] uppercase tracking-wider text-slate-500">Producers of this table</div>
              <div className="flex flex-wrap gap-1.5">
                {producerNodes.map((e) => (
                  <button key={e.id} onClick={() => pickProducer(e.entity_type || "PIPELINE", e.entity_id)}
                    className={`text-[11px] px-2 py-1 rounded-lg border transition-colors ${
                      entityId === e.entity_id ? "bg-violet-500/20 border-violet-500/40 text-violet-200"
                      : "bg-surface-100/60 border-white/[0.06] text-slate-300 hover:border-violet-500/30"
                    }`}>
                    {e.display_name || `${e.entity_type} ${e.entity_id}`}
                  </button>
                ))}
              </div>
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
      )}

      {/* ---- History tab ---- */}
      {effectiveTab === "history" && (
        allVersions.length === 0 ? (
          <div className="text-[11px] text-slate-500 py-6 text-center">No analysis versions yet. Run an LLM analysis or capture a plan to build history.</div>
        ) : (
          <div className="space-y-2.5">
            {/* All versions, source-tagged — click to view that version's columns */}
            <div className="rounded-lg border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04] max-h-40 overflow-y-auto">
              {allVersions.map((v) => {
                const isPlan = v.source === "plan_capture";
                const active = viewingVersion?.ref === v.ref;
                return (
                  <button key={v.ref} onClick={() => viewVersion(v.ref)}
                    className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-left transition-colors ${
                      active ? "bg-accent/15" : "bg-surface-100/40 hover:bg-white/[0.04]"}`}>
                    <span className={`text-[8px] px-1.5 py-0.5 rounded border uppercase tracking-wide shrink-0 ${
                      isPlan ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/25"
                             : "bg-violet-500/15 text-violet-300 border-violet-500/25"}`}>
                      {isPlan ? "plan" : "llm"}
                    </span>
                    <span className={`text-[11px] truncate flex-1 ${active ? "text-accent-light" : "text-slate-200"}`}>{v.label}</span>
                    {viewLoading === v.ref && <Loader2 size={11} className="animate-spin text-accent shrink-0" />}
                    {v.analyzed_at && <span className="text-[9px] text-slate-600 shrink-0">{v.analyzed_at.slice(0, 10)}</span>}
                  </button>
                );
              })}
            </div>

            {/* Viewing a specific version's columns */}
            {viewingVersion && (
              <div className="rounded-lg border border-accent/25 bg-accent/[0.04] overflow-hidden">
                <div className="flex items-center gap-2 px-2.5 py-1.5 border-b border-white/[0.06]">
                  <History size={12} className="text-accent-light" />
                  <span className="text-[11px] font-medium text-slate-100">{viewingVersion.label}</span>
                  <span className="text-[9px] text-slate-500">{viewingVersion.columns.length} cols</span>
                  <button onClick={() => setViewingVersion(null)} className="ml-auto text-[10px] text-slate-500 hover:text-accent-light">
                    ✕ back to current
                  </button>
                </div>
                <div className="divide-y divide-white/[0.04] max-h-72 overflow-y-auto">
                  {viewingVersion.columns.length === 0 && (
                    <div className="px-2.5 py-2 text-[10px] text-slate-600">This version has no per-column detail.</div>
                  )}
                  {viewingVersion.columns.map((c, i) => <ColumnCard key={i} c={c} />)}
                </div>
              </div>
            )}

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
        )
      )}

      {/* ---- Producers tab (only when 2+ producers) ---- */}
      {effectiveTab === "producers" && hasProducers && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-[12px] font-semibold text-slate-100">
            <Columns3 size={14} className="text-amber-400 shrink-0" />
            {producerNodes.length} producers write this table
          </div>
          <p className="text-[10px] text-slate-500">
            Each may compute the same column differently — compare to catch divergent logic.
          </p>
          {pcLoading && (
            <div className="flex items-center justify-center gap-2 py-6 text-slate-400">
              <Loader2 size={15} className="animate-spin text-accent" />
              <span className="text-[11px]">Resolving {producerNodes.length} producers…</span>
            </div>
          )}
          {!pcLoading && producerCmp && (
            <ProducerCompareMatrix cmp={producerCmp} onRefresh={() => runProducerCompare(true)} />
          )}
          {!pcLoading && !producerCmp && (
            <button onClick={() => runProducerCompare(false)}
              className="flex items-center gap-1.5 text-[11px] px-2.5 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 border border-amber-500/30 text-amber-900 dark:text-amber-100 font-medium transition-colors">
              <Columns3 size={12} /> Compare side-by-side
            </button>
          )}
        </div>
      )}

      {/* Wide AI overview modal */}
      {overviewOpen && table && (
        <ColumnOverviewModal
          table={table}
          entityType={entityId ? entityType : undefined}
          entityId={entityId || undefined}
          onClose={() => setOverviewOpen(false)}
        />
      )}
    </div>
  );
}
