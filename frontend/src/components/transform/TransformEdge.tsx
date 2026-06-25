/**
 * TransformEdge — custom React Flow edge that always shows the transformation.
 *
 * Visual design:
 * - Animated dashed stroke colored by transformation category
 * - A PERSISTENT label at the edge midpoint shows the category badge + a
 *   truncated SQL/PySpark expression (so "how was this column derived" is
 *   visible at a glance, no hover required)
 * - Hover expands the label into a full card with the complete expression
 *   and the source file path
 */
import React, { useState } from 'react';
import { EdgeProps, getBezierPath, EdgeLabelRenderer, useStore } from 'reactflow';

interface TransformEdgeData {
  expression: string;
  category: string;
  categoryColor: string;
  sourceFile: string;
}

const MAX_INLINE_EXPR = 24;

function truncate(expr: string, max: number): string {
  if (!expr) return '';
  return expr.length > max ? `${expr.slice(0, max - 1)}…` : expr;
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
  // Current viewport zoom — used to counter-scale the label so its on-screen
  // size stays stable regardless of zoom (prevents the label ballooning).
  const zoom = useStore((s) => s.transform[2]);

  const [edgePath] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature: 0.3,
  });

  // Anchor the label 35% along the source→target vector (not the midpoint), so
  // when several edges converge on the SAME target column their labels inherit
  // each source's distinct X and spread apart instead of stacking on top of
  // each other.
  const lblX = sourceX + (targetX - sourceX) * 0.35;
  const lblY = sourceY + (targetY - sourceY) * 0.35;

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

      {data && (
        <EdgeLabelRenderer>
          <div
            className="absolute z-50"
            style={{
              transform: `translate(-50%, -50%) translate(${lblX}px, ${lblY}px) scale(${1 / zoom})`,
              transformOrigin: 'center',
            }}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
          >
            {hovered ? (
              /* Expanded detail card */
              <div className="bg-slate-900/95 border border-slate-600 rounded-lg p-3 shadow-xl backdrop-blur-sm max-w-[360px] pointer-events-auto">
                <span
                  className="inline-block px-2 py-0.5 rounded text-[10px] font-bold tracking-wide mb-2"
                  style={{ backgroundColor: `${color}25`, color }}
                >
                  {data.category}
                </span>
                <div className="mt-1">
                  <p className="text-[9px] text-slate-500 font-semibold uppercase tracking-wider mb-1">
                    Expression
                  </p>
                  <code className="text-[11px] text-amber-300 font-mono break-all leading-relaxed block">
                    {data.expression}
                  </code>
                </div>
                {data.sourceFile && data.sourceFile !== '?' && (
                  <div className="mt-2 pt-2 border-t border-slate-700">
                    <p className="text-[9px] text-slate-500 truncate" title={data.sourceFile}>
                      📄 {data.sourceFile.split('/').pop()}
                    </p>
                  </div>
                )}
              </div>
            ) : (
              /* Persistent compact label — always visible */
              <div
                className="flex items-center gap-1.5 px-2 py-1 rounded-md border bg-slate-900/90 backdrop-blur-sm shadow-md cursor-default max-w-[200px]"
                style={{ borderColor: `${color}66` }}
              >
                <span
                  className="px-1.5 py-0.5 rounded text-[9px] font-bold tracking-wide whitespace-nowrap shrink-0"
                  style={{ backgroundColor: `${color}25`, color }}
                >
                  {data.category}
                </span>
                {data.expression && (
                  <code
                    className="text-[10px] text-amber-300/90 font-mono truncate"
                    title={data.expression}
                  >
                    {truncate(data.expression, MAX_INLINE_EXPR)}
                  </code>
                )}
              </div>
            )}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
