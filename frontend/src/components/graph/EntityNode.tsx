import { memo, useEffect, useState } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { motion } from "framer-motion";
import { Play, GitBranch, FileText, Terminal, User, Clock, CheckCircle, AlertTriangle, DollarSign } from "lucide-react";
import { api } from "../../api/client";
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

function EntityNodeComponent({ data }: NodeProps<EntityNodeData>) {
  const isRevealed = data.isRevealed ?? true;
  const isDimmed = data.isDimmed ?? false;
  const Icon = entityIcons[data.entity_type] || Terminal;
  const discountPercent = useLineageStore((s) => s.discountPercent);

  const [displayName, setDisplayName] = useState(data.display_name);
  const [owner, setOwner] = useState(data.owner);
  const [showTooltip, setShowTooltip] = useState(false);

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
            <span className="font-mono font-bold text-[12px] text-emerald-300 bg-emerald-500/10 px-1.5 py-0.5 rounded flex-shrink-0">
              ${costDisplay}
            </span>
          )}
          <span className={`text-[8px] font-semibold tracking-wider uppercase px-1.5 py-0.5 rounded ${badgeColor} bg-white/[0.04]`}>
            {data.entity_type}
          </span>
        </div>
      </div>

      {/* Hover tooltip — z-index raised above sibling nodes */}
      {showTooltip && !isDimmed && (
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
