/**
 * TransformEdge — custom React Flow edge with expression tooltip.
 *
 * Visual design:
 * - Animated dashed stroke colored by transformation category
 * - Hover reveals a tooltip showing:
 *   - Category badge (ARITHMETIC, WINDOW, AGGREGATE, etc.)
 *   - The actual SQL/PySpark expression
 *   - Source file path
 */
import React, { useState } from 'react';
import { EdgeProps, getBezierPath, EdgeLabelRenderer } from 'reactflow';

interface TransformEdgeData {
  expression: string;
  category: string;
  categoryColor: string;
  sourceFile: string;
}

export default function TransformEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  style,
}: EdgeProps<TransformEdgeData>) {
  const [hovered, setHovered] = useState(false);

  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.3,
  });

  const color = data?.categoryColor || '#6B7280';

  return (
    <>
      {/* Invisible wider path for easier hover targeting */}
      <path
        d={edgePath}
        fill="none"
        stroke="transparent"
        strokeWidth={16}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
      />

      {/* Visible animated edge */}
      <path
        id={id}
        d={edgePath}
        fill="none"
        stroke={color}
        strokeWidth={hovered ? 3 : 2}
        strokeDasharray={hovered ? 'none' : '6 4'}
        strokeOpacity={hovered ? 1 : 0.7}
        style={{
          ...style,
          transition: 'stroke-width 0.2s, stroke-opacity 0.2s',
          filter: hovered ? `drop-shadow(0 0 6px ${color})` : 'none',
        }}
        className="animated-edge"
      />

      {/* Expression tooltip on hover */}
      {hovered && data && (
        <EdgeLabelRenderer>
          <div
            className="absolute pointer-events-none z-50"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            }}
          >
            <div className="bg-slate-900/95 border border-slate-600 rounded-lg p-3 shadow-xl backdrop-blur-sm max-w-[360px]">
              {/* Category badge */}
              <span
                className="inline-block px-2 py-0.5 rounded text-[10px] font-bold tracking-wide mb-2"
                style={{ backgroundColor: `${color}25`, color }}
              >
                {data.category}
              </span>

              {/* Expression */}
              <div className="mt-1">
                <p className="text-[9px] text-slate-500 font-semibold uppercase tracking-wider mb-1">
                  Expression
                </p>
                <code className="text-[11px] text-amber-300 font-mono break-all leading-relaxed block">
                  {data.expression}
                </code>
              </div>

              {/* Source file */}
              {data.sourceFile && data.sourceFile !== '?' && (
                <div className="mt-2 pt-2 border-t border-slate-700">
                  <p className="text-[9px] text-slate-500 truncate" title={data.sourceFile}>
                    \uD83D\uDCC4 {data.sourceFile.split('/').pop()}
                  </p>
                </div>
              )}
            </div>
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
