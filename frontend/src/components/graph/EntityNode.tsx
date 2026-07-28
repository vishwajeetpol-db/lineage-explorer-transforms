import { memo, useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { motion } from "framer-motion";
import {
  Play, GitBranch, FileText, Terminal, User, Clock, CheckCircle, AlertTriangle,
  DollarSign, Activity, Loader2, ExternalLink, XCircle, TrendingUp, TrendingDown, RefreshCw,
} from "lucide-react";
import { api, type EntityRuns } from "../../api/client";
import { useLineageStore } from "../../store/lineageStore";

type EntityNodeData = {
  node_type: "entity";
  entity_type: string;
  entity_id: string;
  display_name: string | null;
  last_run: string | null;
  owner: string | null;
  cost_usd: number | null;
  isRevealed?: boolean;
  isDimmed?: boolean;
  isHighlighted?: boolean;
  isSelected?: boolean;
};

const entityIcons: Record<string, typeof Play> = {
  JOB: Play,
  PIPELINE: GitBranch,
  NOTEBOOK: FileText,
  QUERY: Terminal,
};

function isFresh(lastRun: string | null): boolean {
  if (!lastRun) return false;
  const runDate = new Date(lastRun).toDateString();
  const today = new Date().toDateString();
  return runDate === today;
}

function formatTimeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function fmtDuration(secs: number | null): string {
  if (secs == null) return "—";
  secs = Math.round(secs); // avg_duration_seconds can be fractional
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60);
  if (m < 60) return `${m}m ${secs % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

const VERDICT_META: Record<string, { label: string; cls: string }> = {
  healthy: { label: "Healthy", cls: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30" },
  degraded: { label: "Degraded", cls: "bg-amber-500/15 text-amber-300 border-amber-500/30" },
  failing: { label: "Failing", cls: "bg-rose-500/15 text-rose-300 border-rose-500/30" },
  unknown: { label: "No run history", cls: "bg-surface-200 text-slate-400 border-white/[0.08]" },
};

/** Health-check popover for a JOB/PIPELINE node: verdict + success rate,
 *  duration trend, cost total (+ spike flag), and the last N runs with per-run
 *  status, duration, cost, failure reason, and a link to the run. */
function HealthPopover({ entityType, entityId }: { entityType: string; entityId: string }) {
  const [data, setData] = useState<EntityRuns | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const discountPercent = useLineageStore((s) => s.discountPercent);

  const load = (refresh = false) => {
    setLoading(true); setError(null);
    api.getEntityRuns(entityType, entityId, 5, refresh)
      .then(setData)
      .catch((e) => setError(e.message || "Failed to load run health"))
      .finally(() => setLoading(false));
  };
  useEffect(() => { load(false); }, [entityType, entityId]);

  const disc = (c: number | null) => (c == null ? null : c * (1 - discountPercent / 100));
  const verdict = VERDICT_META[data?.verdict || "unknown"];

  return (
    <div
      className="bg-surface-100/95 backdrop-blur-xl border border-white/[0.1] rounded-xl shadow-[0_8px_32px_rgba(0,0,0,0.6)] w-[320px] overflow-hidden"
      onClick={(e) => e.stopPropagation()}
    >
      {/* Header */}
      <div className="flex items-center gap-2 px-3.5 py-2.5 border-b border-white/[0.06]">
        <Activity size={13} className="text-accent" />
        <span className="text-[12px] font-semibold text-slate-100">Run health</span>
        {data && (
          <span className={`text-[9px] px-1.5 py-0.5 rounded border uppercase tracking-wide ${verdict.cls}`}>
            {verdict.label}
          </span>
        )}
        <button
          onClick={() => load(true)}
          disabled={loading}
          title="Refresh run health"
          className="ml-auto text-slate-500 hover:text-accent-light transition-colors disabled:opacity-40"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
        </button>
      </div>

      <div className="px-3.5 py-3 space-y-3 max-h-[360px] overflow-y-auto">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-6 text-slate-500">
            <Loader2 size={16} className="animate-spin text-accent" /> <span className="text-[11px]">Checking…</span>
          </div>
        )}
        {error && <div className="text-[11px] text-rose-300 bg-rose-500/10 border border-rose-500/25 rounded-lg px-2.5 py-2">{error}</div>}

        {data && !loading && (
          <>
            {/* Summary row */}
            <div className="grid grid-cols-3 gap-2">
              <div className="rounded-lg bg-surface-200/50 px-2 py-1.5 text-center">
                <div className="text-[15px] font-semibold text-slate-100 leading-none">
                  {data.success_rate != null ? `${Math.round(data.success_rate * 100)}%` : "—"}
                </div>
                <div className="text-[8px] uppercase tracking-wider text-slate-500 mt-1">Success</div>
              </div>
              <div className="rounded-lg bg-surface-200/50 px-2 py-1.5 text-center">
                <div className="text-[15px] font-semibold text-slate-100 leading-none flex items-center justify-center gap-1">
                  {fmtDuration(data.avg_duration_seconds)}
                  {data.duration_trend === "up" && <TrendingUp size={11} className="text-rose-400" />}
                  {data.duration_trend === "down" && <TrendingDown size={11} className="text-emerald-400" />}
                </div>
                <div className="text-[8px] uppercase tracking-wider text-slate-500 mt-1">Avg dur</div>
              </div>
              <div
                className="rounded-lg bg-surface-200/50 px-2 py-1.5 text-center"
                title={`Summed cost of the ${data.runs.length} run${data.runs.length !== 1 ? "s" : ""} below. The node badge shows a separate 30-day serverless total for the whole ${data.entity_type.toLowerCase()}.`}
              >
                <div className="text-[15px] font-semibold text-emerald-300 leading-none">
                  {disc(data.total_cost_usd) != null ? `$${disc(data.total_cost_usd)!.toFixed(2)}` : "—"}
                </div>
                <div className="text-[8px] uppercase tracking-wider text-slate-500 mt-1">Last {data.runs.length} runs</div>
              </div>
            </div>

            {/* Runs list */}
            {data.runs.length === 0 ? (
              <div className="text-[11px] text-slate-500 text-center py-3">
                {data.detail || `No runs in the last ${data.lookback_days} days.`}
              </div>
            ) : (
              <div className="rounded-lg border border-white/[0.06] divide-y divide-white/[0.04] overflow-hidden">
                {data.runs.map((r, i) => {
                  const spike = r.run_id && r.run_id === data.cost_spike_run_id;
                  const c = disc(r.cost_usd);
                  return (
                    <div key={r.run_id || i} className="px-2.5 py-1.5 space-y-1">
                      <div className="flex items-center gap-2">
                        {r.succeeded
                          ? <CheckCircle size={12} className="text-emerald-400 shrink-0" />
                          : <XCircle size={12} className="text-rose-400 shrink-0" />}
                        <span className="text-[10px] font-mono text-slate-300 truncate flex-1">
                          {r.result_state || "—"}
                        </span>
                        <Clock size={9} className="text-slate-600" />
                        <span className="text-[10px] text-slate-400">{fmtDuration(r.duration_seconds)}</span>
                        {c != null && (
                          <span className={`text-[10px] font-mono ${spike ? "text-orange-300" : "text-emerald-300/80"}`}>
                            ${c.toFixed(2)}{spike && " ⚠"}
                          </span>
                        )}
                        {r.run_url && (
                          <a href={r.run_url} target="_blank" rel="noreferrer" title="Open run"
                            className="text-slate-500 hover:text-accent-light shrink-0">
                            <ExternalLink size={10} />
                          </a>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-[9px] text-slate-600">
                        {r.started_at && <span>{r.started_at.slice(0, 16).replace("T", " ")}</span>}
                        {!r.succeeded && r.result_state && (
                          <span className="text-rose-400/70 truncate">· {r.result_state}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}

            {data.entity_url && (
              <a href={data.entity_url} target="_blank" rel="noreferrer"
                className="flex items-center justify-center gap-1.5 text-[10px] text-accent-light hover:underline pt-0.5">
                <ExternalLink size={10} /> Open {entityType.toLowerCase()} in Databricks
              </a>
            )}
            {data._cache?.from_cache && (
              <div className="text-[9px] text-slate-600 text-center">cached · refresh for live</div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function EntityNodeComponent({ data }: NodeProps<EntityNodeData>) {
  const isRevealed = data.isRevealed ?? true;
  const isDimmed = data.isDimmed ?? false;
  const Icon = entityIcons[data.entity_type] || Terminal;
  const discountPercent = useLineageStore((s) => s.discountPercent);

  const [displayName, setDisplayName] = useState(data.display_name);
  const [owner, setOwner] = useState(data.owner);
  const [showTooltip, setShowTooltip] = useState(false);
  const [showHealth, setShowHealth] = useState(false);
  // Health check is only meaningful for runnable producers.
  const hasHealth = data.entity_type === "JOB" || data.entity_type === "PIPELINE";

  const fresh = isFresh(data.last_run);
  const dotColor = fresh ? "bg-emerald-400" : "bg-amber-400";
  const dotShadow = fresh ? "shadow-emerald-400/40" : "shadow-amber-400/40";
  const iconColor = fresh ? "text-emerald-400" : "text-amber-400";
  const badgeColor = fresh ? "text-emerald-400" : "text-amber-400";
  const borderColor = fresh ? "border-emerald-500/25" : "border-amber-500/25";
  const bgGradient = fresh
    ? "from-emerald-500/[0.08] to-emerald-600/[0.04]"
    : "from-amber-500/[0.08] to-amber-600/[0.04]";

  // Lazy-fetch display name and owner
  useEffect(() => {
    if (displayName) return;
    api.getEntityName(data.entity_type, data.entity_id)
      .then((r) => {
        setDisplayName(r.name);
        if (r.owner) setOwner(r.owner);
      })
      .catch(() => setDisplayName(`${data.entity_type} ${data.entity_id.slice(0, 8)}…`));
  }, [data.entity_type, data.entity_id, displayName]);

  const label = displayName || `${data.entity_type} ${data.entity_id.slice(0, 8)}…`;

  // Compute discounted cost — purely client-side math
  const costRaw = data.cost_usd;
  const costDisplay = costRaw != null
    ? (costRaw * (1 - discountPercent / 100)).toFixed(2)
    : null;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9 }}
      animate={{
        opacity: isRevealed ? (isDimmed ? 0.15 : 1) : 0,
        scale: isRevealed ? 1 : 0.9,
      }}
      transition={{ duration: 0.25, ease: "easeOut" }}
      className="relative"
      style={{ zIndex: showTooltip ? 1000 : undefined }}
      onMouseEnter={() => setShowTooltip(true)}
      onMouseLeave={() => setShowTooltip(false)}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !rounded-full !border-0 !bg-slate-600"
      />
      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !rounded-full !border-0 !bg-slate-600"
      />

      {/* Main node */}
      <div
        className={`
          px-3.5 py-2 rounded-xl backdrop-blur-sm
          shadow-[0_2px_12px_rgba(0,0,0,0.3)]
          border bg-gradient-to-r ${bgGradient} ${borderColor}
          ${isDimmed ? "pointer-events-none" : ""}
        `}
        style={{ minWidth: 160 }}
      >
        <div className="flex items-center gap-2.5">
          <div className={`w-1.5 h-1.5 rounded-full ${dotColor} shadow-[0_0_6px] ${dotShadow} flex-shrink-0`} />
          <Icon size={13} className={`${iconColor} flex-shrink-0 opacity-70`} />
          <span className="font-mono font-medium text-[11px] text-slate-300 truncate max-w-[140px]">
            {label}
          </span>
          {costDisplay && (
            <span
              title="30-day serverless total for this entity. Open the health check (activity icon) for per-run costs."
              className="font-mono font-bold text-[12px] text-emerald-300 bg-emerald-500/10 px-1.5 py-0.5 rounded flex-shrink-0"
            >
              ${costDisplay}
            </span>
          )}
          <span className={`text-[8px] font-semibold tracking-wider uppercase px-1.5 py-0.5 rounded ${badgeColor} bg-white/[0.04]`}>
            {data.entity_type}
          </span>
          {hasHealth && (
            <button
              onClick={(e) => { e.stopPropagation(); setShowHealth((v) => !v); setShowTooltip(false); }}
              title="Run health check"
              className={`flex-shrink-0 rounded p-0.5 transition-colors ${
                showHealth ? "text-accent-light bg-accent/15" : "text-slate-500 hover:text-accent-light hover:bg-white/[0.06]"
              }`}
            >
              <Activity size={12} />
            </button>
          )}
        </div>
      </div>

      {/* Health-check popover */}
      {showHealth && hasHealth && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="absolute left-1/2 -translate-x-1/2 top-full mt-2 z-[1001]"
        >
          <HealthPopover entityType={data.entity_type} entityId={data.entity_id} />
        </motion.div>
      )}

      {/* Hover tooltip — z-index raised above sibling nodes */}
      {showTooltip && !isDimmed && !showHealth && (
        <motion.div
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          className="absolute left-1/2 -translate-x-1/2 bottom-full mb-2 z-[1000]"
        >
          <div className="bg-surface-100/95 backdrop-blur-xl border border-white/[0.08] rounded-xl px-4 py-3 shadow-[0_8px_32px_rgba(0,0,0,0.5)] min-w-[240px]">
            <div className="font-mono text-[12px] text-slate-200 font-medium mb-2 truncate">{label}</div>
            <div className="space-y-1.5 text-[11px]">
              {owner && (
                <div className="flex items-center gap-2 text-slate-400">
                  <User size={11} className="flex-shrink-0" />
                  <span className="truncate">{owner}</span>
                </div>
              )}
              {data.last_run && (
                <div className="flex items-center gap-2 text-slate-400">
                  <Clock size={11} className="flex-shrink-0" />
                  <span>Last run {formatTimeAgo(data.last_run)}</span>
                </div>
              )}
              <div className={`flex items-center gap-2 ${fresh ? "text-emerald-400" : "text-amber-400"}`}>
                {fresh ? <CheckCircle size={11} /> : <AlertTriangle size={11} />}
                <span>{fresh ? "Downstream lineage data is current" : "Downstream lineage data may be stale"}</span>
              </div>
              {costRaw != null && (
                <div className="flex items-center gap-2 text-emerald-400 pt-1 border-t border-white/[0.06] mt-1">
                  <DollarSign size={11} className="flex-shrink-0" />
                  <span className="font-mono font-bold">${costDisplay}</span>
                  {discountPercent > 0 && (
                    <span className="text-slate-500 text-[10px]">(list ${costRaw.toFixed(2)} - {discountPercent}%)</span>
                  )}
                  <span className="text-slate-600 text-[10px]">30d serverless</span>
                </div>
              )}
            </div>
          </div>
        </motion.div>
      )}
    </motion.div>
  );
}

export default memo(EntityNodeComponent);
