import { memo, useCallback, useMemo, useRef } from "react";
import { Handle, Position, type NodeProps } from "reactflow";
import { motion, AnimatePresence } from "framer-motion";
import { Database, Eye, Layers, ChevronDown, Key, ExternalLink, HardDrive, FolderOpen, Zap, Share2, Building2, Microscope } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { useTransformStore } from "../../store/transformStore";
import type { TableNode as TableNodeType } from "../../api/client";

// Injected by the Delta Sharing overlay (see LineageCanvas). Optional — only
// present when the Sharing toggle is on and this table participates in a share.
export type SharingBadge = {
  out?: { shares: string[]; recipients: string[] };
  in?: { provider: string; shares: string[] };
};

const typeConfig: Record<string, { color: string; bg: string; border: string; icon: typeof Database; label: string; dot: string }> = {
  MANAGED: { color: "text-blue-400", bg: "bg-blue-500/10", border: "border-blue-500/25", icon: Database, label: "TABLE", dot: "bg-blue-400" },
  TABLE: { color: "text-blue-400", bg: "bg-blue-500/10", border: "border-blue-500/25", icon: Database, label: "TABLE", dot: "bg-blue-400" },
  EXTERNAL: { color: "text-blue-400", bg: "bg-blue-500/10", border: "border-blue-500/25", icon: Database, label: "TABLE", dot: "bg-blue-400" },
  VIEW: { color: "text-emerald-400", bg: "bg-emerald-500/10", border: "border-emerald-500/25", icon: Eye, label: "VIEW", dot: "bg-emerald-400" },
  MATERIALIZED_VIEW: { color: "text-amber-400", bg: "bg-amber-500/10", border: "border-amber-500/25", icon: Layers, label: "MAT VIEW", dot: "bg-amber-400" },
  EXTERNAL_LINEAGE: { color: "text-cyan-400", bg: "bg-cyan-500/10", border: "border-cyan-500/25", icon: ExternalLink, label: "CROSS-SCHEMA", dot: "bg-cyan-400" },
  STREAMING_TABLE: { color: "text-rose-400", bg: "bg-rose-500/10", border: "border-rose-500/25", icon: Zap, label: "STREAMING", dot: "bg-rose-400" },
  VOLUME: { color: "text-violet-400", bg: "bg-violet-500/10", border: "border-violet-500/25", icon: FolderOpen, label: "VOLUME", dot: "bg-violet-400" },
  PATH: { color: "text-orange-400", bg: "bg-orange-500/10", border: "border-orange-500/25", icon: HardDrive, label: "STORAGE", dot: "bg-orange-400" },
};

function TableNodeComponent({ data, id }: NodeProps<TableNodeType & { isExpanded: boolean; isSelected: boolean; isHighlighted: boolean; isDimmed: boolean; isRevealed?: boolean; sharingBadge?: SharingBadge }>) {
  // Field selectors, NOT whole-store destructuring: a table node must re-render
  // only when column mode / selected column change — not on every hover/select/
  // loading change. With hundreds of nodes, whole-store subscription = every node
  // re-rendering on each hover (and memo() can't help — the hook fires first).
  const columnLineageEnabled = useLineageStore((s) => s.columnLineageEnabled);
  const selectedColumn = useLineageStore((s) => s.selectedColumn);
  const toggleNodeExpanded = useLineageStore((s) => s.toggleNodeExpanded);
  const setSelectedColumn = useLineageStore((s) => s.setSelectedColumn);
  const setHoveredNode = useLineageStore((s) => s.setHoveredNode);
  const config = typeConfig[data.table_type] || typeConfig.MANAGED;
  const Icon = config.icon;
  const isExpanded = data.isExpanded;
  const isSelected = data.isSelected;
  const isDimmed = data.isDimmed;
  const isRevealed = data.isRevealed ?? true;
  const isOrphan = data.lineage_status === "orphan";
  const isCrossSchema = ["EXTERNAL_LINEAGE", "VOLUME", "PATH"].includes(data.table_type);

  const handleNodeClick = useCallback(() => {
    if (columnLineageEnabled) {
      toggleNodeExpanded(id);
    }
  }, [columnLineageEnabled, id, toggleNodeExpanded]);

  const handleColumnClick = useCallback(
    (colName: string, e: React.MouseEvent) => {
      e.stopPropagation();
      if (selectedColumn?.table === id && selectedColumn?.column === colName) {
        setSelectedColumn(null);
      } else {
        setSelectedColumn({ table: id, column: colName });
      }
    },
    [id, selectedColumn, setSelectedColumn]
  );

  // Transformation lineage drill-down: opens the TransformPanel for the selected column
  const handleTransformDrillDown = useCallback(
    (colName: string, e: React.MouseEvent) => {
      e.stopPropagation();
      // id is the full table name (catalog.schema.table)
      useTransformStore.getState().openPanel(id, colName);
    },
    [id]
  );

  const columns: { name: string; type: string; nullable: boolean }[] = useMemo(() => data.columns || [], [data.columns]);
  const hoverTimer = useRef<ReturnType<typeof setTimeout>>();

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{
        opacity: isRevealed ? (isDimmed ? 0.2 : 1) : 0,
        scale: isRevealed ? 1 : 0.95,
      }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className={`
        relative rounded-2xl border transition-all duration-300
        ${isSelected
          ? "border-accent/60 shadow-[0_0_0_1px_rgba(99,102,241,0.3),0_0_30px_rgba(99,102,241,0.15),0_8px_32px_rgba(0,0,0,0.5)]"
          : isCrossSchema
            ? "border-cyan-500/40 border-dashed shadow-[0_4px_24px_rgba(0,0,0,0.4)] hover:border-cyan-500/60"
            : isOrphan
              ? "border-amber-500/30 shadow-[0_4px_24px_rgba(0,0,0,0.4)] hover:border-amber-500/50"
              : "border-white/[0.06] hover:border-white/[0.12] shadow-[0_4px_24px_rgba(0,0,0,0.4)] hover:shadow-[0_8px_40px_rgba(0,0,0,0.5)]"
        }
        ${isDimmed ? "pointer-events-none" : "cursor-pointer"}
        bg-gradient-to-b from-[#161625] to-[#12121E]
      `}
      onClick={handleNodeClick}
      onMouseEnter={() => { clearTimeout(hoverTimer.current); hoverTimer.current = setTimeout(() => setHoveredNode(id), 80); }}
      onMouseLeave={() => { clearTimeout(hoverTimer.current); setHoveredNode(null); }}
    >
      {/* Left handle — table-level target */}
      <Handle
        type="target"
        position={Position.Left}
        id={`${id}__table__target`}
        className="!w-3 !h-3 !rounded-full !bg-[#1E1E2E] !border-2 !border-white/10 hover:!border-accent/60 !-left-[7px] !transition-colors !duration-200"
      />

      {/* Header */}
      <div className="flex items-center gap-2.5 px-4 py-3">
        {/* Type dot indicator */}
        <div className={`w-2 h-2 rounded-full ${config.dot} shadow-[0_0_6px] ${config.dot.replace('bg-', 'shadow-')}/40 flex-shrink-0`} />

        <Icon size={15} className={`${config.color} flex-shrink-0 opacity-60`} />

        <span className="font-mono font-medium text-[13px] text-slate-100 flex-1 leading-tight">
          {data.name}
        </span>

        <span className={`text-[9px] font-bold tracking-wider px-2 py-0.5 rounded-full ${config.bg} ${config.color} border ${config.border} uppercase flex-shrink-0`}>
          {config.label}
        </span>

        {/* Delta Sharing badges — outbound (shared to recipients) / inbound (shared-in) */}
        {data.sharingBadge?.out && (
          <span
            className="flex items-center gap-1 text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-teal-500/10 text-teal-300 border border-teal-400/30 flex-shrink-0"
            title={`Shared via ${data.sharingBadge.out.shares.join(", ")}${data.sharingBadge.out.recipients.length ? ` → ${data.sharingBadge.out.recipients.join(", ")}` : ""}`}
          >
            <Share2 size={9} />
            {data.sharingBadge.out.recipients.length || data.sharingBadge.out.shares.length}
          </span>
        )}
        {data.sharingBadge?.in && (
          <span
            className="flex items-center gap-1 text-[9px] font-bold px-1.5 py-0.5 rounded-full bg-violet-500/10 text-violet-300 border border-violet-400/30 flex-shrink-0"
            title={`Shared in from provider ${data.sharingBadge.in.provider}`}
          >
            <Building2 size={9} />
            shared-in
          </span>
        )}

        {columnLineageEnabled && columns.length > 0 && (
          <motion.div
            animate={{ rotate: isExpanded ? 0 : -90 }}
            transition={{ duration: 0.2 }}
            className="flex-shrink-0"
          >
            <ChevronDown size={14} className="text-slate-600" />
          </motion.div>
        )}
      </div>

      {/* Expanded columns */}
      <AnimatePresence initial={false}>
        {isExpanded && columns.length > 0 && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.3, ease: [0.4, 0, 0.2, 1] }}
            className="overflow-hidden"
          >
            {/* Separator */}
            <div className="mx-3 h-px bg-gradient-to-r from-transparent via-white/[0.06] to-transparent" />

            <div className="py-1.5 px-1">
              {columns.map((col, idx) => {
                const isColSelected =
                  selectedColumn?.table === id && selectedColumn?.column === col.name;
                const isPK = idx === 0 && col.name.toLowerCase().endsWith("_id");

                return (
                  <motion.div
                    key={col.name}
                    initial={{ opacity: 0, x: -6 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: idx * 0.02, duration: 0.15 }}
                    onClick={(e) => handleColumnClick(col.name, e)}
                    className={`
                      relative flex items-center gap-2 px-3 py-[5px] rounded-lg mx-0.5 text-[12px] cursor-pointer
                      transition-all duration-150
                      ${isColSelected
                        ? "bg-purple-500/15 shadow-[inset_0_0_0_1px_rgba(139,92,246,0.35)]"
                        : "hover:bg-white/[0.03]"
                      }
                    `}
                  >
                    {/* Column handles */}
                    <Handle
                      type="target"
                      position={Position.Left}
                      id={`${id}__col__${col.name}__target`}
                      className={`!w-2 !h-2 !rounded-full !border-0 !-left-[5px] ${isColSelected ? "!bg-purple-400 !opacity-100" : "!bg-transparent !opacity-0"}`}
                      style={{ top: "auto" }}
                    />

                    {isPK ? (
                      <Key size={11} className="text-amber-400/70 flex-shrink-0" />
                    ) : (
                      <span className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${isColSelected ? "bg-purple-400" : "bg-white/10"}`} />
                    )}

                    <span className={`font-mono flex-1 truncate ${isColSelected ? "text-purple-200 font-medium" : "text-slate-400"}`}>
                      {col.name}
                    </span>
                    <span className={`text-[10px] font-mono tracking-wide ${isColSelected ? "text-purple-400/60" : "text-slate-600"}`}>
                      {col.type}
                    </span>

                    {/* Transformation drill-down icon */}
                    {isColSelected && (
                      <motion.button
                        initial={{ opacity: 0, scale: 0.8 }}
                        animate={{ opacity: 1, scale: 1 }}
                        onClick={(e) => handleTransformDrillDown(col.name, e)}
                        className="p-0.5 rounded hover:bg-purple-500/20 text-purple-400 hover:text-purple-300 transition-colors"
                        title="View transformation lineage"
                      >
                        <Microscope size={11} />
                      </motion.button>
                    )}

                    <Handle
                      type="source"
                      position={Position.Right}
                      id={`${id}__col__${col.name}__source`}
                      className={`!w-2 !h-2 !rounded-full !border-0 !-right-[5px] ${isColSelected ? "!bg-purple-400 !opacity-100" : "!bg-transparent !opacity-0"}`}
                      style={{ top: "auto" }}
                    />
                  </motion.div>
                );
              })}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Right handle — table-level source */}
      <Handle
        type="source"
        position={Position.Right}
        id={`${id}__table__source`}
        className="!w-3 !h-3 !rounded-full !bg-[#1E1E2E] !border-2 !border-white/10 hover:!border-accent/60 !-right-[7px] !transition-colors !duration-200"
      />
    </motion.div>
  );
}

export default memo(TableNodeComponent);
