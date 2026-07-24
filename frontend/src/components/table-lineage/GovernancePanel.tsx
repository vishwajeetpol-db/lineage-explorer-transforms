import { useCallback, useEffect, useState } from "react";
import { Tag, Plus, X, Lock } from "lucide-react";
import { api, type GovernanceResponse, type GovernanceRule } from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";
import { PanelState, NoTable, SectionTitle, SensitivityBadge, parseFqn } from "./panelShared";

const SENSITIVITIES = ["PII", "PCI", "PHI", "SENSITIVE", "CONFIDENTIAL"];

/** Governance — modeled on the POC's panel: table identity + tags + classified
 *  columns, plus a "Configure classification" section to add column- and
 *  UC-tag-based classification rules (admin-gated). */

function IdRow({ label, value }: { label: string; value: string | null }) {
  return (
    <div>
      <div className="text-[9px] uppercase tracking-wider text-slate-600 font-medium">{label}</div>
      <div className="text-[11px] text-slate-300 font-mono truncate">{value || "—"}</div>
    </div>
  );
}

export default function GovernancePanel({ table }: { table: string | null }) {
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const [data, setData] = useState<GovernanceResponse | null>(null);
  const [rules, setRules] = useState<GovernanceRule[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Column-classification form state
  const [colTarget, setColTarget] = useState("__whole_table__");
  const [colSensitivity, setColSensitivity] = useState("PII");
  // Tag-rule form state
  const [tagKey, setTagKey] = useState("");
  const [tagValue, setTagValue] = useState("");
  const [tagSensitivity, setTagSensitivity] = useState("PII");

  const parts = parseFqn(table);

  const loadGovernance = useCallback(() => {
    if (!parts) { setData(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    api.getGovernance(parts.catalog, parts.schema, parts.table)
      .then((r) => { if (!cancelled) setData(r); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load governance"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [table]);

  const loadRules = useCallback(() => {
    api.listGovernanceRules().then((r) => setRules(r.rules)).catch(() => setRules([]));
  }, []);

  useEffect(() => { loadGovernance(); }, [loadGovernance]);
  useEffect(() => { loadRules(); }, [loadRules]);

  if (!table) return <NoTable />;

  const addColumnRule = async () => {
    if (!parts) return;
    setBusy(true); setActionError(null);
    try {
      await api.upsertGovernanceRule({
        catalog: parts.catalog,
        schema_name: parts.schema,
        table_pattern: parts.table,
        column_pattern: colTarget === "__whole_table__" ? ".*" : `^${colTarget}$`,
        sensitivity: colSensitivity,
        notes: colTarget === "__whole_table__" ? "whole table" : `column ${colTarget}`,
      });
      loadRules(); loadGovernance();
    } catch (e: any) {
      setActionError(e.message || "Failed to add rule");
    } finally { setBusy(false); }
  };

  const addTagRule = async () => {
    if (!tagKey.trim()) return;
    setBusy(true); setActionError(null);
    try {
      await api.upsertGovernanceRule({
        tag_name: tagKey.trim(),
        sensitivity: tagSensitivity,
        notes: tagValue.trim() ? `tag ${tagKey}=${tagValue}` : `tag ${tagKey} (any value)`,
      });
      setTagKey(""); setTagValue("");
      loadRules(); loadGovernance();
    } catch (e: any) {
      setActionError(e.message || "Failed to add rule");
    } finally { setBusy(false); }
  };

  const removeRule = async (ruleId: string) => {
    setBusy(true); setActionError(null);
    try {
      await api.deleteGovernanceRule(ruleId);
      loadRules(); loadGovernance();
    } catch (e: any) {
      setActionError(e.message || "Failed to delete rule");
    } finally { setBusy(false); }
  };

  return (
    <PanelState loading={loading} error={error} empty={!data}>
      {data && (
        <div className="space-y-4">
          {/* Identity header */}
          <div className="rounded-xl bg-surface-100/60 border border-white/[0.06] px-3 py-3 grid grid-cols-2 gap-y-2.5 gap-x-3">
            <IdRow label="Owner" value={data.owner} />
            <IdRow label="Type" value={data.table_type} />
            <IdRow label="Created by" value={data.created_by} />
            <IdRow label="Last altered" value={data.last_altered_at} />
          </div>

          {data.comment && (
            <div className="text-[11px] text-slate-400 italic px-1">{data.comment}</div>
          )}

          {/* Table tags */}
          <div>
            <SectionTitle>Table tags</SectionTitle>
            {data.tags.length > 0 ? (
              <div className="flex flex-wrap gap-1.5">
                {data.tags.map((t, i) => (
                  <span key={i} className="inline-flex items-center gap-1 text-[11px] px-2 py-1 rounded-lg bg-surface-100/60 border border-white/[0.06] text-slate-300">
                    <Tag size={11} className="text-violet-400" /> {t.name}{t.value ? `: ${t.value}` : ""}
                  </span>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-600">No Unity Catalog tags on this table.</div>
            )}
          </div>

          {/* Classified columns */}
          <div>
            <SectionTitle>Classified columns</SectionTitle>
            {data.sensitive_columns.length > 0 ? (
              <div className="rounded-xl border border-white/[0.06] overflow-hidden divide-y divide-white/[0.04]">
                {data.sensitive_columns.map((c) => (
                  <div key={c.column} className="flex items-center gap-2 px-3 py-2 bg-surface-100/40">
                    <span className="font-mono text-[11px] text-slate-200 truncate flex-1">{c.column}</span>
                    <span className="text-[9px] text-slate-600 shrink-0">{c.source}</span>
                    <SensitivityBadge sensitivity={c.sensitivity} />
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-slate-600">No tagged/classified columns.</div>
            )}
          </div>

          {/* Configure classification */}
          <div className="rounded-xl bg-violet-500/[0.05] border border-violet-500/20 px-3 py-3 space-y-4">
            <div className="text-[12px] font-medium text-violet-200">Configure classification</div>

            {!isAdmin && (
              <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                <Lock size={11} /> Workspace admin required to add or remove rules.
              </div>
            )}

            {/* Classify a column / whole table */}
            <div>
              <div className="text-[10px] text-slate-500 mb-1.5">Classify this table / a column</div>
              <div className="flex gap-1.5">
                <select
                  value={colTarget}
                  onChange={(e) => setColTarget(e.target.value)}
                  disabled={!isAdmin}
                  className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 focus:border-accent/50 outline-none disabled:opacity-40"
                >
                  <option value="__whole_table__">(whole table)</option>
                  {data.columns.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
                <select
                  value={colSensitivity}
                  onChange={(e) => setColSensitivity(e.target.value)}
                  disabled={!isAdmin}
                  className="px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 focus:border-accent/50 outline-none disabled:opacity-40"
                >
                  {SENSITIVITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                <button
                  onClick={addColumnRule}
                  disabled={!isAdmin || busy}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200 text-[11px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                >
                  <Plus size={11} /> Add
                </button>
              </div>
            </div>

            {/* Tag → classification rules */}
            <div>
              <div className="text-[10px] text-slate-500 mb-0.5">Tag → classification rules</div>
              <div className="text-[9px] text-slate-600 mb-1.5">
                Any asset carrying this UC tag is auto-classified (value blank = match any value).
              </div>
              <div className="flex gap-1.5">
                <input
                  value={tagKey}
                  onChange={(e) => setTagKey(e.target.value)}
                  disabled={!isAdmin}
                  placeholder="tag key (e.g. pii)"
                  className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 font-mono placeholder:text-slate-600 focus:border-accent/50 outline-none disabled:opacity-40"
                />
                <input
                  value={tagValue}
                  onChange={(e) => setTagValue(e.target.value)}
                  disabled={!isAdmin}
                  placeholder="value (optional)"
                  className="flex-1 min-w-0 px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 font-mono placeholder:text-slate-600 focus:border-accent/50 outline-none disabled:opacity-40"
                />
                <select
                  value={tagSensitivity}
                  onChange={(e) => setTagSensitivity(e.target.value)}
                  disabled={!isAdmin}
                  className="px-2 py-1.5 bg-surface-100 border border-white/[0.08] rounded-lg text-[11px] text-slate-200 focus:border-accent/50 outline-none disabled:opacity-40"
                >
                  {SENSITIVITIES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
                <button
                  onClick={addTagRule}
                  disabled={!isAdmin || busy || !tagKey.trim()}
                  className="flex items-center gap-1 px-2.5 py-1.5 rounded-lg bg-violet-500/15 hover:bg-violet-500/25 border border-violet-500/30 text-violet-200 text-[11px] font-medium transition-all disabled:opacity-40 disabled:cursor-not-allowed shrink-0"
                >
                  <Plus size={11} /> Add
                </button>
              </div>
            </div>

            {actionError && (
              <div className="text-[10px] text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-2 py-1.5 break-words">
                {actionError}
              </div>
            )}

            {/* Existing rules */}
            {rules.length > 0 && (
              <div className="space-y-1">
                {rules.map((r) => {
                  const label = r.tag_name
                    ? `${r.tag_name}${r.notes?.includes("=") ? `=${r.notes.split("=")[1]}` : ""} → ${r.sensitivity}`
                    : `${r.column_pattern === ".*" ? "(whole table)" : r.column_pattern} → ${r.sensitivity}`;
                  return (
                    <div key={r.rule_id} className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg bg-surface-100/60 border border-white/[0.06]">
                      <span className="font-mono text-[10px] text-slate-300 truncate flex-1">{label}</span>
                      {isAdmin && (
                        <button
                          onClick={() => removeRule(r.rule_id)}
                          disabled={busy}
                          className="text-slate-500 hover:text-rose-400 transition-colors shrink-0 disabled:opacity-40"
                          title="Remove rule"
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </PanelState>
  );
}
