/**
 * TransformNode — custom React Flow node for column-level transformation graph.
 *
 * Visual design:
 * - Circular pulse node (matching LATTICE's "cyber-organic" aesthetic)
 * - Glowing border colored by depth level
 * - Shows column name prominently, table name subtly
 * - Target column (depth 0) has a distinct "bullseye" treatment
 */
import React, { memo } from 'react';
import { Handle, Position, NodeProps } from 'reactflow';
import { motion } from 'framer-motion';

interface TransformNodeData {
  column: string;
  tableFqn: string;
  shortTable: string;
  depth: number;
  color: string;
  label: string;
  isTarget: boolean;
}

function TransformNodeComponent({ data }: NodeProps<TransformNodeData>) {
  const { column, shortTable, color, isTarget, depth } = data;

  return (
    <>
      <Handle type="target" position={Position.Bottom} className="!bg-transparent !border-0" />
      <motion.div
        initial={{ scale: 0.8, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ duration: 0.3, delay: depth * 0.05 }}
        className={`
          relative flex flex-col items-center justify-center
          px-3 py-2 rounded-xl
          border-2 backdrop-blur-sm
          cursor-pointer select-none
          transition-all duration-200
          hover:scale-105 hover:shadow-lg
          ${isTarget
            ? 'bg-red-950/80 border-red-500 shadow-red-500/30 shadow-lg'
            : 'bg-slate-900/80 border-slate-600 hover:border-opacity-100'
          }
        `}
        style={{
          borderColor: isTarget ? undefined : color,
          boxShadow: `0 0 12px ${color}30, 0 0 4px ${color}20`,
          minWidth: '140px',
          maxWidth: '200px',
        }}
      >
        {/* Pulse ring for target node */}
        {isTarget && (
          <motion.div
            className="absolute inset-0 rounded-xl border-2 border-red-500"
            animate={{ scale: [1, 1.08, 1], opacity: [0.6, 0, 0.6] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
        )}

        {/* Column name */}
        <span
          className="text-sm font-bold truncate max-w-[176px]"
          style={{ color: isTarget ? '#FF6B4A' : '#E2E8F0' }}
          title={column}
        >
          {column}
        </span>

        {/* Table name (subtle) */}
        <span
          className="text-[10px] text-slate-400 truncate max-w-[176px] mt-0.5"
          title={data.tableFqn}
        >
          {shortTable}
        </span>

        {/* Depth badge */}
        <div
          className="absolute -top-2 -right-2 w-5 h-5 rounded-full flex items-center justify-center text-[9px] font-bold"
          style={{ backgroundColor: color, color: '#000' }}
        >
          {isTarget ? '\u25CE' : depth}
        </div>
      </motion.div>
      <Handle type="source" position={Position.Top} className="!bg-transparent !border-0" />
    </>
  );
}

export default memo(TransformNodeComponent);
