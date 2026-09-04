import { memo } from "react";
import { motion } from "framer-motion";
import { User, Database, Columns3, Calendar, ArrowUpRight, ArrowDownRight, Eye, Layers, AlertCircle, CircleDot, ArrowRightFromLine, ArrowRightToLine } from "lucide-react";
import type { TableNode } from "../../api/client";

interface Props {
  node: TableNode;
  position: { x: number; y: number };
  onMouseEnter?: () => void;
  onMouseLeave?: () => void;
}

const typeIcons: Record<string, { icon: typeof Database; color: string; label: string }> = {
  MANAGED: { icon: Database, color: "text-blue-400", label: "Managed Table" },
  TABLE: { icon: Database, color: "text-blue-400", label: "Table" },
  EXTERNAL: { icon: Database, color: "text-blue-400", label: "External Table" },
  VIEW: { icon: Eye, color: "text-emerald-400", label: "View" },
  MATERIALIZED_VIEW: { icon: Layers, color: "text-amber-400", label: "Materialized View" },
};

function TableTooltip({ node, position, onMouseEnter, onMouseLeave }: Props) {
  const typeInfo = typeIcons[node.table_type] || typeIcons.MANAGED;
  const TypeIcon = typeInfo.icon;

  // Clamp position to stay within viewport
  const clampedX = Math.min(position.x, window.innerWidth - 310);
  const clampedY = Math.max(8, Math.min(position.y, window.innerHeight - 280));

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.96, x: -4 }}
      animate={{ opacity: 1, scale: 1, x: 0 }}
      exit={{ opacity: 0, scale: 0.96 }}
      transition={{ duration: 0.12, ease: "easeOut" }}
      className="fixed z-[9999] pointer-events-auto"
      style={{ left: clampedX + 12, top: clampedY }}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
    >
      <div className="rounded-xl overflow-hidden border border-white/[0.08] shadow-[0_16px_48px_rgba(0,0,0,0.35),0_0_0_1px_rgba(255,255,255,0.04)] backdrop-blur-2xl bg-surface-50/90 min-w-[270px]">
        {/* Header — full name is selectable so it can be copied */}
        <div className="px-4 pt-3.5 pb-2">
          <div className="font-mono font-semibold text-[14px] text-white tracking-tight select-text">
            {node.name}
          </div>
          <div className="font-mono text-[10px] text-slate-500 mt-1 tracking-wide select-text cursor-text break-all">
            {node.full_name}
          </div>
        </div>

        {/* Divider */}
        <div className="mx-4 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

        {/* Details */}
        <div className="px-4 py-3 space-y-2.5">
          <Row icon={<User size={12} />} label="Owner" value={node.owner || "—"} />
          <Row
            icon={<TypeIcon size={12} className={typeInfo.color} />}
            label="Type"
            value={typeInfo.label}
            valueClass={typeInfo.color}
          />
          <Row icon={<Columns3 size={12} />} label="Columns" value={String(node.columns?.length || 0)} />
          {node.created_at && (
            <Row icon={<Calendar size={12} />} label="Created" value={formatDate(node.created_at)} />
          )}
          {node.updated_at && (
            <Row icon={<Calendar size={12} />} label="Updated" value={formatDate(node.updated_at)} />
          )}
        </div>

        {/* Lineage counts */}
        <div className="mx-4 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
        <div className="px-4 py-2.5 flex gap-5">
          <CountBadge
            icon={<ArrowDownRight size={12} className="text-emerald-400" />}
            label="Upstream"
            count={node.upstream_count}
          />
          <CountBadge
            icon={<ArrowUpRight size={12} className="text-blue-400" />}
            label="Downstream"
            count={node.downstream_count}
          />
        </div>

        {/* Lineage status */}
        {node.lineage_status && node.lineage_status !== "connected" && (
          <>
            <div className="mx-4 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />
            <div className="px-4 py-2.5">
              <LineageStatusBadge status={node.lineage_status} />
            </div>
          </>
        )}
      </div>
    </motion.div>
  );
}

function Row({ icon, label, value, valueClass = "text-slate-200" }: {
  icon: React.ReactNode;
  label: string;
  value: string;
  valueClass?: string;
}) {
  return (
    <div className="flex items-center gap-2.5 text-[12px]">
      <span className="text-slate-500">{icon}</span>
      <span className="text-slate-500 w-16 text-[11px]">{label}</span>
      <span className={`font-medium truncate text-[12px] ${valueClass}`}>{value}</span>
    </div>
  );
}

function CountBadge({ icon, label, count }: { icon: React.ReactNode; label: string; count: number }) {
  return (
    <div className="flex items-center gap-1.5 text-[11px]">
      {icon}
      <span className="text-slate-500">{label}</span>
      <span className="text-slate-200 font-semibold ml-0.5">{count}</span>
    </div>
  );
}

const lineageStatusConfig: Record<string, { icon: typeof AlertCircle; color: string; bg: string; message: string; link?: string }> = {
  orphan: {
    icon: AlertCircle,
    color: "text-amber-400",
    bg: "bg-amber-500/10",
    message: "No lineage recorded. No tracked query has read from or written to this table.",
  },
  root: {
    icon: ArrowRightFromLine,
    color: "text-emerald-400",
    bg: "bg-emerald-500/10",
    message: "Source table \u2014 no upstream dependencies.",
  },
  leaf: {
    icon: ArrowRightToLine,
    color: "text-blue-400",
    bg: "bg-blue-500/10",
    message: "Sink table \u2014 no downstream consumers.",
  },
};

function LineageStatusBadge({ status }: { status: string }) {
  const config = lineageStatusConfig[status];
  if (!config) return null;
  const Icon = config.icon;
  return (
    <div className={`flex items-start gap-2 rounded-lg px-3 py-2 ${config.bg}`}>
      <Icon size={13} className={`${config.color} flex-shrink-0 mt-0.5`} />
      <div className="flex flex-col gap-1">
        <span className={`text-[11px] leading-relaxed ${config.color}`}>{config.message}</span>
        {config.link && (
          <a
            href={config.link}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-slate-500 underline underline-offset-2 hover:text-slate-400 pointer-events-auto"
          >
            UC lineage limitations
          </a>
        )}
      </div>
    </div>
  );
}

function formatDate(d: string): string {
  try {
    return new Date(d).toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    });
  } catch {
    return d;
  }
}

export default memo(TableTooltip);
