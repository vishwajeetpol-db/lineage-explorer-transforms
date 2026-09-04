import { useCallback, useEffect, useState } from "react";
import { ShieldOff } from "lucide-react";
import { api, type AccessResponse } from "../../api/client";
import { PanelState, NoTable, SectionTitle, CacheHeader, parseFqn } from "./panelShared";

/** Access & security — modeled on the POC's panel: a stat row (grantees /
 *  accessors / reads / writes), identity metadata, declared grants, empirical
 *  accessors, and a recent-events feed. */

function privilegeBadge(priv: string) {
  const p = priv.toUpperCase();
  const cls =
    p === "ALL_PRIVILEGES" || p === "ALL PRIVILEGES"
      ? "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30"
      : p === "MANAGE"
      ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
      : p === "SELECT"
      ? "bg-emerald-500/15 text-emerald-300 border-emerald-500/30"
      : "bg-surface-200 text-slate-400 border-white/[0.08]";
  return (
    <span key={priv} className={`text-[9px] px-1.5 py-0.5 rounded border font-medium uppercase tracking-wide ${cls}`}>
      {priv}
    </span>
  );
}

function StatTile({ label, value, accent = "text-slate-100" }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="rounded-xl bg-surface-100/60 border border-white/[0.06] px-2 py-2.5 text-center">
      <div className={`text-[20px] font-semibold leading-none ${accent}`}>{value}</div>
      <div className="text-[8px] uppercase tracking-wider text-slate-500 font-medium mt-1.5">{label}</div>
    </div>
  );
}

function IdentityRow({ label, value }: { label: string; value: string | null }) {
  if (!value) return null;
  return (
    <div className="flex items-baseline gap-2">
      <span className="text-[9px] uppercase tracking-wider text-slate-600 font-medium w-24 shrink-0">{label}</span>
      <span className="text-[11px] text-slate-300 font-mono truncate">{value}</span>
    </div>
  );
}

// Group declared grants by principal so each identity shows all its privileges together.
function groupGrants(grants: AccessResponse["declared_grants"]) {
  const map = new Map<string, string[]>();
  for (const g of grants) {
    if (!g.principal) continue;
    if (!map.has(g.principal)) map.set(g.principal, []);
    if (g.privilege) map.get(g.principal)!.push(g.privilege);
  }
  return Array.from(map.entries()).map(([principal, privileges]) => ({ principal, privileges }));
}

export default function AccessPanel({ table }: { table: string | null }) {
  const [data, setData] = useState<AccessResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback((refresh = false) => {
    const parts = parseFqn(table);
    if (!parts) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    api.getAccess(parts.catalog, parts.schema, parts.table, refresh)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load access"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [table]);

  useEffect(() => { load(false); }, [load]);

  if (!table) return <NoTable />;

  return (
    <>
      <CacheHeader cache={data?._cache} loading={loading} onRefresh={() => load(true)} />
    <PanelState loading={loading} error={error} empty={!data}>
      {data && (
        <div className="space-y-4">
          {/* Stat row */}
          <div className="grid grid-cols-4 gap-2">
            <StatTile label="Grantees" value={data.grantee_count} accent="text-emerald-300" />
            <StatTile label={`Accessors (${data.lookback_days}d)`} value={data.unique_empirical_users} accent="text-sky-300" />
            <StatTile label="Reads" value={data.read_count} />
            <StatTile label="Writes" value={data.write_count} accent={data.write_count > 0 ? "text-amber-300" : "text-slate-100"} />
          </div>

          {/* Identities */}
          {(data.identities.owner || data.identities.created_by || data.identities.last_altered_by) && (
            <div>
              <SectionTitle>Identities</SectionTitle>
              <div className="rounded-xl bg-surface-100/60 border border-white/[0.06] px-3 py-2.5 space-y-1.5">
                <IdentityRow label="Owner" value={data.identities.owner} />
                <IdentityRow label="Created by" value={data.identities.created_by} />
                <IdentityRow label="Created at" value={data.identities.created_at} />
                <IdentityRow label="Last altered by" value={data.identities.last_altered_by} />
                <IdentityRow label="Last altered" value={data.identities.last_altered_at} />
              </div>
            </div>
          )}

          {/* Dormant grants callout */}
          {data.dormant_grants.length > 0 && (
            <div>
              <SectionTitle>Dormant grants (SELECT, never used)</SectionTitle>
              <div className="flex flex-wrap gap-1.5">
                {data.dormant_grants.map((p) => (
                  <span key={p} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-amber-500/10 border border-amber-500/25 text-amber-200">
                    <ShieldOff size={11} /> {p}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Who can access (grants) */}
          <div>
            <SectionTitle>Who can access (grants)</SectionTitle>
            <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
              {data.declared_grants.length === 0 && (
                <div className="px-3 py-2 text-[11px] text-slate-600 bg-surface-100/40">No declared grants visible.</div>
              )}
              {groupGrants(data.declared_grants).map((g) => (
                <div key={g.principal} className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">
                  <span className="font-mono text-[11px] text-slate-200 truncate flex-1">{g.principal}</span>
                  <div className="flex flex-wrap gap-1 justify-end shrink-0">
                    {g.privileges.map((p) => privilegeBadge(p))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Who has accessed it */}
          <div>
            <SectionTitle>Who has accessed it ({data.lookback_days}d)</SectionTitle>
            <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
              {data.audit_access.length === 0 && (
                <div className="px-3 py-2 text-[11px] text-slate-600 bg-surface-100/40">No audit access in window.</div>
              )}
              {data.audit_access.map((a, i) => (
                <div key={i} className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">
                  <span className="font-mono text-[11px] text-slate-200 truncate flex-1">{a.user_email}</span>
                  {a.access_count != null && (
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-surface-200 text-slate-400 shrink-0">{a.access_count}×</span>
                  )}
                  {a.action_name && <span className="text-[10px] text-slate-500 shrink-0">{a.action_name}</span>}
                </div>
              ))}
            </div>
          </div>

          {/* Recent events */}
          {data.recent_events.length > 0 && (
            <div>
              <SectionTitle>Recent events</SectionTitle>
              <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                {data.recent_events.map((e, i) => (
                  <div key={i} className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">
                    <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-surface-200 text-slate-400 shrink-0">{e.action_name}</span>
                    <span className="font-mono text-[11px] text-slate-300 truncate flex-1">{e.user_email || "—"}</span>
                    {e.source_ip && <span className="text-[10px] text-slate-600 font-mono shrink-0 hidden sm:inline">{e.source_ip}</span>}
                    <span className="text-[10px] text-slate-500 shrink-0">{e.event_time?.slice(0, 16).replace("T", " ")}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </PanelState>
    </>
  );
}
