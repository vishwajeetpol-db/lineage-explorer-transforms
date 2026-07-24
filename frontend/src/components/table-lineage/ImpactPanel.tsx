import { useEffect, useState } from "react";
import { ShieldAlert, Users, ExternalLink, BarChart3, Settings, Workflow, FileCode, Search as SearchIcon } from "lucide-react";
import { api, type ImpactResponse, type ConsumerEntity } from "../../api/client";
import { PanelState, NoTable, Stat, SectionTitle, parseFqn } from "./panelShared";

// Icon + friendly label per lineage entity_type.
const TYPE_META: Record<string, { label: string; icon: typeof BarChart3; color: string }> = {
  DASHBOARD_V3: { label: "Dashboards", icon: BarChart3, color: "text-fuchsia-400" },
  DASHBOARD: { label: "Dashboards", icon: BarChart3, color: "text-fuchsia-400" },
  DBSQL_DASHBOARD: { label: "Dashboards", icon: BarChart3, color: "text-fuchsia-400" },
  JOB: { label: "Jobs", icon: Settings, color: "text-amber-400" },
  PIPELINE: { label: "Pipelines", icon: Workflow, color: "text-sky-400" },
  NOTEBOOK: { label: "Notebooks", icon: FileCode, color: "text-emerald-400" },
  QUERY: { label: "Queries", icon: SearchIcon, color: "text-violet-400" },
  DBSQL_QUERY: { label: "Queries", icon: SearchIcon, color: "text-violet-400" },
};

function typeMeta(t: string) {
  return TYPE_META[t] || { label: t.replace(/_/g, " "), icon: FileCode, color: "text-slate-400" };
}

function ConsumerRow({ c }: { c: ConsumerEntity }) {
  const m = typeMeta(c.entity_type);
  const Icon = m.icon;
  const name = c.display_name || `${c.entity_type} ${c.entity_id}`;
  const inner = (
    <>
      <Icon size={13} className={`${m.color} shrink-0`} />
      <span className="text-[11px] text-slate-300 truncate flex-1">{name}</span>
      {c.deep_link && <ExternalLink size={11} className="text-slate-500 group-hover:text-accent-light shrink-0" />}
    </>
  );
  return c.deep_link ? (
    <a
      href={c.deep_link}
      target="_blank"
      rel="noreferrer"
      className="group flex items-center gap-2 px-3 py-2 bg-surface-100/40 hover:bg-accent/[0.06] transition-colors"
    >
      {inner}
    </a>
  ) : (
    <div className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">{inner}</div>
  );
}

export default function ImpactPanel({ table }: { table: string | null }) {
  const [data, setData] = useState<ImpactResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const parts = parseFqn(table);
    if (!parts) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    api.getImpact(parts.catalog, parts.schema, parts.table)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load impact"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [table]);

  if (!table) return <NoTable />;

  const consumers = data?.consumers;
  const byType = consumers?.by_type || {};

  return (
    <PanelState loading={loading} error={error} empty={!data} emptyLabel="No downstream impact found.">
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Downstream tables" value={data.downstream_count} accent="text-rose-300" />
            <Stat label="Consumers" value={consumers?.total ?? 0} accent="text-sky-300" />
            <Stat label="Owners" value={data.consumer_owners.length} />
            <Stat label="Sensitive" value={data.sensitive_affected_count} accent={data.sensitive_affected_count > 0 ? "text-orange-300" : "text-slate-300"} />
          </div>

          {/* Consumers by type — badge row */}
          {Object.keys(byType).length > 0 && (
            <div>
              <SectionTitle>Consumers by type</SectionTitle>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(byType).map(([t, n]) => {
                  const m = typeMeta(t);
                  const Icon = m.icon;
                  return (
                    <span key={t} className="inline-flex items-center gap-1.5 text-[11px] px-2 py-1 rounded-lg bg-surface-100/60 border border-white/[0.06] text-slate-300">
                      <Icon size={11} className={m.color} /> {n} {m.label}
                    </span>
                  );
                })}
              </div>
            </div>
          )}

          {/* Consumer entities — clickable deep-links */}
          {consumers && consumers.entities.length > 0 && (
            <div>
              <SectionTitle>Consumers ({consumers.total})</SectionTitle>
              <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                {consumers.entities.map((c, i) => <ConsumerRow key={`${c.entity_type}:${c.entity_id}:${i}`} c={c} />)}
              </div>
              {consumers.total > consumers.entities.length && (
                <div className="text-[10px] text-slate-600 mt-1.5">
                  Showing {consumers.entities.length} of {consumers.total} consumers.
                </div>
              )}
            </div>
          )}

          {data.consumer_owners.length > 0 && (
            <div>
              <SectionTitle>Affected owners</SectionTitle>
              <div className="flex flex-wrap gap-1.5">
                {data.consumer_owners.map((o) => (
                  <span key={o} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-surface-100/60 border border-white/[0.06] text-slate-300">
                    <Users size={11} className="text-sky-400" /> {o}
                  </span>
                ))}
              </div>
            </div>
          )}

          <div>
            <SectionTitle>Downstream tables ({data.downstream_tables.length})</SectionTitle>
            <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
              {data.downstream_tables.map((t) => (
                <div key={t.full_name} className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">
                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-surface-200 text-slate-400 shrink-0">
                    hop {t.hop_distance}
                  </span>
                  <span className="font-mono text-[11px] text-slate-300 truncate flex-1">{t.full_name}</span>
                  {t.has_sensitive_columns && <ShieldAlert size={13} className="text-orange-400 shrink-0" />}
                  {t.owner && <span className="text-[10px] text-slate-500 truncate max-w-[120px] shrink-0">{t.owner}</span>}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </PanelState>
  );
}
