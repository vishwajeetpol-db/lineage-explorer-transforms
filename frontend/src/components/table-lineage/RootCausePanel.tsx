import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Clock, CheckCircle2, HelpCircle, ArrowRight } from "lucide-react";
import { api, type RootCauseTrace, type ProducerHealthStatus, type FlaggedTable } from "../../api/client";
import { PanelState, NoTable, SectionTitle, CacheHeader, parseFqn } from "./panelShared";

/** Root-cause trace — modeled on the POC's health-based "Root-cause trace":
 *  auto-runs on table select, walks upstream producers, classifies each by run
 *  health, and surfaces a prime suspect + failure path + flagged producers. */

const STATUS_META: Record<ProducerHealthStatus, { label: string; cls: string; dot: string; icon: typeof AlertTriangle }> = {
  failed: { label: "failed", cls: "bg-rose-500/15 text-rose-300 border-rose-500/30", dot: "bg-rose-400", icon: AlertTriangle },
  stale: { label: "stale", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30", dot: "bg-amber-400", icon: Clock },
  healthy: { label: "healthy", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", dot: "bg-emerald-400", icon: CheckCircle2 },
  no_history: { label: "no history", cls: "bg-surface-200 text-slate-400 border-white/[0.08]", dot: "bg-slate-500", icon: HelpCircle },
};

function StatusPill({ status, count }: { status: ProducerHealthStatus; count: number }) {
  const m = STATUS_META[status];
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-full border font-medium ${m.cls}`}>
      <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} /> {count} {m.label}
    </span>
  );
}

function StatusDot({ status }: { status: ProducerHealthStatus }) {
  return <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_META[status].dot}`} />;
}

function fmtWhen(ts: string | null) {
  if (!ts) return "";
  return ts.slice(0, 16).replace("T", " ");
}

function ProducerRow({ p }: { p: FlaggedTable["producers"][number] }) {
  return (
    <div className="flex items-center gap-2 pl-3 pr-1 py-1">
      <StatusDot status={p.status} />
      <span className="font-mono text-[10px] text-slate-400 truncate flex-1">
        {p.entity_type} {p.entity_id}
      </span>
      {p.success_rate != null && (
        <span className="text-[9px] text-slate-500 shrink-0">{Math.round(p.success_rate * 100)}% ok</span>
      )}
      {p.last_run_at && <span className="text-[9px] text-slate-600 shrink-0">{fmtWhen(p.last_run_at)}</span>}
    </div>
  );
}

export default function RootCausePanel({ table }: { table: string | null }) {
  const [data, setData] = useState<RootCauseTrace | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((refresh = false) => {
    const parts = parseFqn(table);
    if (!parts) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    api.getRootCauseTrace(parts.catalog, parts.schema, parts.table, undefined, refresh)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to run trace"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [table]);

  useEffect(() => { load(false); }, [load]);

  if (!table) return <NoTable />;

  const prime = data?.prime_suspect;

  return (
    <>
      <CacheHeader cache={data?._cache} loading={loading} onRefresh={() => load(true)} />
    <PanelState loading={loading} error={error} empty={!data} emptyLabel="No producers to trace.">
      {data && (
        <div className="space-y-4">
          {/* Status pills */}
          <div className="flex flex-wrap gap-1.5">
            <StatusPill status="failed" count={data.counts.failed} />
            <StatusPill status="stale" count={data.counts.stale} />
            <StatusPill status="healthy" count={data.counts.healthy} />
            <StatusPill status="no_history" count={data.counts.no_history} />
          </div>

          {/* Prime suspect */}
          {prime && (
            <div className={`rounded-xl border px-3 py-3 ${prime.status === "failed" ? "bg-rose-500/[0.06] border-rose-500/25" : prime.status === "stale" ? "bg-amber-500/[0.06] border-amber-500/25" : "bg-surface-100/60 border-white/[0.08]"}`}>
              <div className="flex items-center gap-1.5 mb-1">
                <StatusDot status={prime.status} />
                <span className="text-[11px] font-semibold text-slate-100">
                  Prime suspect · {STATUS_META[prime.status].label}
                </span>
              </div>
              <div className="font-mono text-[12px] text-slate-200">{prime.short_name}</div>
              <div className="text-[10px] text-slate-500 mt-0.5">
                {prime.is_focus ? "focus table" : `${prime.hop} hop${prime.hop !== 1 ? "s" : ""} upstream`}
              </div>
              <div className="mt-2 rounded-lg bg-black/20 border border-white/[0.05] divide-y divide-white/[0.04]">
                {prime.producers.map((p, i) => <ProducerRow key={i} p={p} />)}
              </div>
            </div>
          )}

          {/* Failure path */}
          {data.failure_path.length > 0 && (
            <div>
              <SectionTitle>Failure path → focus</SectionTitle>
              <div className="flex flex-wrap items-center gap-1">
                {data.failure_path.map((n, i) => (
                  <span key={n.table} className="inline-flex items-center gap-1">
                    <span className={`inline-flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg border font-mono ${STATUS_META[n.status].cls}`}>
                      <StatusDot status={n.status} /> {n.short_name}
                    </span>
                    {i < data.failure_path.length - 1 && <ArrowRight size={11} className="text-slate-600" />}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* All flagged producers */}
          <div>
            <SectionTitle>All flagged producers ({data.flagged.length})</SectionTitle>
            <div className="space-y-2">
              {data.flagged.map((f) => (
                <div key={f.table} className="rounded-xl bg-surface-100/50 border border-white/[0.06] overflow-hidden">
                  <div className="flex items-center gap-2 px-3 py-2">
                    <StatusDot status={f.status} />
                    <span className="font-mono text-[11px] text-slate-200 truncate flex-1">{f.short_name}</span>
                    {f.is_focus && <span className="text-[8px] uppercase tracking-wider text-slate-500 shrink-0">focus</span>}
                    {!f.is_focus && <span className="text-[9px] text-slate-600 shrink-0">+{f.hop}</span>}
                  </div>
                  <div className="border-t border-white/[0.04] divide-y divide-white/[0.03]">
                    {f.producers.map((p, i) => <ProducerRow key={i} p={p} />)}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </PanelState>
    </>
  );
}
