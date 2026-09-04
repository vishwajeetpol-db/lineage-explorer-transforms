import { useEffect, useState } from "react";
import { Boxes, FileCode } from "lucide-react";
import { api, type MlModel } from "../../api/client";
import { PanelState, NoTable, parseFqn } from "./panelShared";

export default function MLModelsPanel({ table }: { table: string | null }) {
  const [models, setModels] = useState<MlModel[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const parts = parseFqn(table);
    if (!parts) { setModels(null); return; }
    let cancelled = false;
    setLoading(true); setError(null);
    api.getMlModelsForTable(parts.catalog, parts.schema, parts.table)
      .then((r) => { if (!cancelled) setModels(r.models); })
      .catch((e) => { if (!cancelled) setError(e.message || "Failed to load ML models"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [table]);

  if (!table) return <NoTable />;

  return (
    <PanelState
      loading={loading}
      error={error}
      empty={!models || models.length === 0}
      emptyLabel="No ML models trained on this table."
    >
      {models && (
        <div className="space-y-2">
          <div className="text-[11px] text-slate-500 mb-1">
            {models.length} model{models.length !== 1 && "s"} trained on this table
          </div>
          {models.map((m, i) => (
            <div key={i} className="rounded-xl bg-surface-100/60 border border-white/[0.06] px-4 py-3 space-y-1.5">
              <div className="flex items-center gap-2">
                <Boxes size={14} className="text-cyan-400 shrink-0" />
                <span className="font-mono text-[12px] text-slate-100 truncate flex-1">{m.model_name}</span>
                {m.model_version && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-cyan-500/15 text-cyan-300 border border-cyan-500/25 shrink-0">
                    v{m.model_version}
                  </span>
                )}
              </div>
              {m.notebook_path && (
                <div className="flex items-center gap-1.5 text-[10px] text-slate-500">
                  <FileCode size={11} /> <span className="truncate">{m.notebook_path}</span>
                </div>
              )}
              {/* Serving status — matches the reference tool's "served by an endpoint" line */}
              {m.endpoints && m.endpoints.length > 0 ? (
                <div className="flex flex-wrap items-center gap-1 text-[10px]">
                  <span className="text-slate-500">served by:</span>
                  {m.endpoints.map((ep) => (
                    <span key={ep} className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-300 border border-emerald-500/25">{ep}</span>
                  ))}
                </div>
              ) : (
                <div className="text-[10px] text-slate-600 italic">Not currently served by an endpoint</div>
              )}
              <div className="flex gap-3 text-[10px] text-slate-600">
                {m.registered_by && <span className="truncate">by {m.registered_by}</span>}
                {m.run_id && <span className="truncate">run {String(m.run_id).slice(0, 12)}</span>}
              </div>
            </div>
          ))}
        </div>
      )}
    </PanelState>
  );
}
