/**
 * PruningControls — interactive filtering toolbar for the transformation graph.
 *
 * The graph always shows the column's FULL end-to-end transformation lineage
 * (no depth knob — depth isn't a meaningful choice when you're inspecting one
 * column's derivation). Controls are limited to context-relevant ones:
 * 1. Category filter — hide/show edge types (client-side)
 * 2. Path isolation indicator — shows when a path is isolated, with clear button
 */
import { useMemo } from 'react';
import { motion } from 'framer-motion';
import { Filter, Route, X, Eye, EyeOff } from 'lucide-react';
import { useTransformStore } from '../../store/transformStore';

/** Category color mapping (subset — full list comes from backend). */
const CATEGORY_COLORS: Record<string, string> = {
  ARITHMETIC: '#FF4433',
  WINDOW: '#A855F7',
  'TYPE CAST': '#10B981',
  CAST: '#10B981',
  AGGREGATE: '#3B82F6',
  AGGREGATION: '#3B82F6',
  STATISTICAL: '#F59E0B',
  PROJECTION: '#6366F1',
  PASSTHROUGH: '#6B7280',
  FILTER: '#EC4899',
  JOIN: '#06B6D4',
  CONDITIONAL: '#F472B6',
  OTHER: '#9CA3AF',
  UNKNOWN: '#6B7280',
};

export default function PruningControls() {
  const hiddenCategories = useTransformStore((s) => s.hiddenCategories);
  const isolatedNodeId = useTransformStore((s) => s.isolatedNodeId);
  const traceResult = useTransformStore((s) => s.traceResult);
  const toggleCategory = useTransformStore((s) => s.toggleCategory);
  const showAllCategories = useTransformStore((s) => s.showAllCategories);
  const hideAllCategories = useTransformStore((s) => s.hideAllCategories);
  const clearIsolation = useTransformStore((s) => s.clearIsolation);

  // Extract distinct categories from the trace result
  const usedCategories = useMemo(() => {
    if (!traceResult) return [];
    const cats = new Set<string>();
    for (const level of traceResult.levels) {
      for (const t of level.transforms) {
        cats.add(t.category);
      }
    }
    return Array.from(cats).sort();
  }, [traceResult]);

  return (
    <div className="px-4 py-3 border-b border-slate-800 space-y-3">
      {/* Path isolation badge (only when a path is isolated) */}
      {isolatedNodeId && (
        <div className="flex items-center">
          <motion.div
            initial={{ opacity: 0, scale: 0.9 }}
            animate={{ opacity: 1, scale: 1 }}
            className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-cyan-900/40 border border-cyan-700/40"
          >
            <Route size={11} className="text-cyan-400" />
            <span className="text-[10px] text-cyan-300 font-medium">Path isolated</span>
            <button
              onClick={clearIsolation}
              className="p-0.5 rounded hover:bg-cyan-700/40 text-cyan-400 hover:text-white transition-colors"
              title="Clear path isolation"
            >
              <X size={10} />
            </button>
          </motion.div>
        </div>
      )}

      {/* Category filter chips */}
      {usedCategories.length > 0 && (
        <div className="flex items-start gap-2">
          <Filter size={13} className="text-slate-500 flex-shrink-0 mt-0.5" />
          <div className="flex flex-wrap gap-1.5 flex-1">
            {usedCategories.map((cat) => {
              const isHidden = hiddenCategories.has(cat);
              const color = CATEGORY_COLORS[cat] || '#6B7280';
              return (
                <button
                  key={cat}
                  onClick={() => toggleCategory(cat)}
                  className={`
                    inline-flex items-center gap-1 px-2 py-0.5 rounded text-[9px] font-bold
                    tracking-wide border transition-all duration-150
                    ${isHidden
                      ? 'opacity-30 border-slate-700 bg-slate-900 text-slate-500 line-through'
                      : 'border-transparent'
                    }
                  `}
                  style={
                    isHidden
                      ? undefined
                      : { backgroundColor: `${color}20`, color, borderColor: `${color}40` }
                  }
                  title={isHidden ? `Show ${cat} edges` : `Hide ${cat} edges`}
                >
                  {isHidden ? <EyeOff size={8} /> : <Eye size={8} />}
                  {cat}
                </button>
              );
            })}
          </div>

          {/* Show all / Hide all */}
          <div className="flex flex-col gap-0.5 flex-shrink-0">
            <button
              onClick={showAllCategories}
              className="text-[8px] text-slate-500 hover:text-slate-300 transition-colors"
              title="Show all categories"
            >
              All
            </button>
            <button
              onClick={hideAllCategories}
              className="text-[8px] text-slate-500 hover:text-slate-300 transition-colors"
              title="Hide all categories"
            >
              None
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
