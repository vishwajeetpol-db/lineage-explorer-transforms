import { useEffect, useState } from "react";
import { ShieldAlert, Users } from "lucide-react";
import { api, type ImpactResponse } from "../../api/client";
import { PanelState, NoTable, Stat, SectionTitle, parseFqn } from "./panelShared";

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

  return (
    <PanelState loading={loading} error={error} empty={!data} emptyLabel="No downstream impact found.">
      {data && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <Stat label="Downstream tables" value={data.downstream_count} accent="text-rose-300" />
            <Stat label="Consumer owners" value={data.consumer_owners.length} accent="text-sky-300" />
            <Stat label="Sensitive affected" value={data.sensitive_affected_count} accent={data.sensitive_affected_count > 0 ? "text-orange-300" : "text-slate-300"} />
            <Stat label="Max hops" value={data.max_hops} />
          </div>

          {data.consumer_owners.length > 0 && (
            <div>
              <SectionTitle>Consumer owners</SectionTitle>
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
                  {t.has_sensitive_columns && (
                    <ShieldAlert size={13} className="text-orange-400 shrink-0" />
                  )}
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
