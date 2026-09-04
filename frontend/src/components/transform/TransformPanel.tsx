/**
 * TransformPanel — slide-out panel for column-level transformation lineage.
 *
 * This is the "microscopic" drill-down experience:
 * 1. User clicks a column's Microscope icon in the table node
 * 2. Panel slides in from the right with Framer Motion
 * 3. Store auto-flows: loading → (building if stale) → ready
 *
 * Store states: closed | loading | needs_build | building | ready | error
 *
 * Transformation lineage is OPT-IN: viewing UC column lineage on the graph is
 * the default and free. Generating transformation logic runs a serverless job
 * (compute cost) and only happens when the user explicitly clicks Generate.
 * Pruning: depth slider, category filter, path isolation (in ready state)
 */
import React from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Zap, RefreshCw, GitBranch, AlertCircle, Layers, Check } from 'lucide-react';
import { useTransformStore } from '../../store/transformStore';
import TransformCanvas from './TransformCanvas';
import BuildProgress from './BuildProgress';
import PruningControls from './PruningControls';

export default function TransformPanel() {
  const panelState = useTransformStore((s) => s.panelState);
  const selectedColumn = useTransformStore((s) => s.selectedColumn);
  const selectedTable = useTransformStore((s) => s.selectedTable);
  const freshness = useTransformStore((s) => s.freshness);
  const buildStatus = useTransformStore((s) => s.buildStatus);
  const traceResult = useTransformStore((s) => s.traceResult);
  const capturedExpression = useTransformStore((s) => s.capturedExpression);
  const capturedExpressionLoading = useTransformStore((s) => s.capturedExpressionLoading);
  const panelError = useTransformStore((s) => s.panelError);
  const closePanel = useTransformStore((s) => s.closePanel);
  const triggerBuild = useTransformStore((s) => s.triggerBuild);

  const isOpen = panelState !== 'closed';

  // Persistent build control (shown in the header). The button is ALWAYS visible
  // once a column is selected so the user can see the build state at a glance:
  //  - not built      → enabled "Generate"
  //  - built but stale → enabled "Regenerate"
  //  - built & fresh   → grayed "Lineage built" (so it's clear it's already built),
  //                      still clickable to force a rebuild
  //  - building/loading → disabled, transient label
  const isBuilt = !!(freshness?.exists && !freshness.is_stale);
  const isStale = !!(freshness?.exists && freshness.is_stale);
  const isBusy = panelState === 'building' || panelState === 'loading';
  const buildLabel = panelState === 'building'
    ? 'Building…'
    : panelState === 'loading'
      ? 'Checking…'
      : isBuilt
        ? 'Lineage built'
        : isStale
          ? 'Regenerate'
          : 'Generate';
  const onBuildClick = () => {
    if (!selectedTable || isBusy) return;
    // force a rebuild when something already exists (stale or fresh)
    triggerBuild(selectedTable, isStale || isBuilt);
  };

  const capturedExpressionCard = (capturedExpressionLoading || capturedExpression) ? (
    <div className="rounded-xl border border-violet-700/40 bg-violet-950/20 p-4">
      <div className="flex flex-wrap items-center gap-2 mb-3">
        <span className="inline-flex items-center rounded-full border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-300">
          Runtime-captured
        </span>
        {capturedExpression?.version != null && (
          <span className="text-[10px] text-slate-500">version {capturedExpression.version}</span>
        )}
        {capturedExpression?.confidence != null && (
          <span className="text-[10px] text-slate-500">confidence {Math.round(capturedExpression.confidence * 100)}%</span>
        )}
      </div>

      {capturedExpressionLoading && !capturedExpression ? (
        <p className="text-xs text-slate-400">Checking for a runtime-captured expression…</p>
      ) : capturedExpression ? (
        <>
          <p className="text-[11px] text-slate-400 mb-2">
            Executed expression for <span className="font-mono text-violet-300">{selectedColumn}</span>
          </p>
          <pre className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-950/70 p-3 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap break-words">
            {capturedExpression.expression}
          </pre>
          <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[10px] text-slate-500">
            {capturedExpression.source_columns?.length > 0 && (
              <span>sources: {capturedExpression.source_columns.join(', ')}</span>
            )}
            {capturedExpression.captured_via && <span>via: {capturedExpression.captured_via}</span>}
            {capturedExpression.captured_at && <span>captured: {capturedExpression.captured_at}</span>}
          </div>
          {capturedExpression.notes && (
            <p className="mt-2 text-[10px] text-slate-500">{capturedExpression.notes}</p>
          )}
        </>
      ) : null}
    </div>
  ) : null;

  return (
    <AnimatePresence>
      {isOpen && (
        <motion.div
          initial={{ x: '100%', opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: '100%', opacity: 0 }}
          transition={{ type: 'spring', damping: 25, stiffness: 200 }}
          className="fixed top-0 right-0 h-full w-[55vw] min-w-[600px] max-w-[900px] bg-slate-950 border-l border-slate-700 shadow-2xl z-50 flex flex-col overflow-hidden"
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-purple-600 to-indigo-600 flex items-center justify-center">
                <GitBranch size={16} className="text-white" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-white tracking-wide">TRANSFORMATION LINEAGE</h2>
                <p className="text-xs text-slate-400">
                  {selectedColumn && selectedTable ? `${selectedTable} › ${selectedColumn}` : 'Select a column'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {selectedTable && panelState !== 'error' && (
                <button
                  onClick={onBuildClick}
                  disabled={isBusy}
                  title={
                    isBuilt
                      ? `Transformation lineage is built (${freshness?.age_str ?? 'cached'}) — click to rebuild`
                      : isStale
                        ? `Transformation lineage may be out of date (${freshness?.age_str ?? ''}) — click to regenerate`
                        : 'Build transformation lineage for this table'
                  }
                  className={
                    `inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors ` +
                    (isBusy
                      ? 'bg-slate-800 text-slate-500 border border-slate-700 cursor-wait'
                      : isBuilt
                        ? 'bg-slate-800/80 text-slate-400 border border-slate-700 hover:bg-slate-700 hover:text-slate-200'
                        : isStale
                          ? 'bg-amber-600/90 hover:bg-amber-500 text-white border border-amber-500/50'
                          : 'bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white shadow-lg shadow-purple-900/40')
                  }
                >
                  {panelState === 'building' || panelState === 'loading' ? (
                    <RefreshCw size={13} className="animate-spin" />
                  ) : isBuilt ? (
                    <Check size={13} />
                  ) : (
                    <Zap size={13} />
                  )}
                  {buildLabel}
                </button>
              )}
              <button onClick={closePanel} className="p-2 rounded-lg hover:bg-slate-800 transition-colors">
                <X size={18} className="text-slate-400" />
              </button>
            </div>
          </div>

          {/* Pruning controls — only when graph is ready with lineage */}
          {panelState === 'ready' && traceResult && !traceResult.is_source_column && traceResult.has_lineage && (
            <PruningControls />
          )}

          {/* Content area */}
          <div className="flex-1 overflow-y-auto p-6">
            {panelState === 'loading' && (
              <div className="flex flex-col items-center justify-center h-full gap-4">
                <RefreshCw size={20} className="text-purple-400 animate-spin" />
                <div className="text-center">
                  <p className="text-sm text-slate-300 font-medium">Checking lineage freshness</p>
                  <p className="text-xs text-slate-500 mt-1">Determining if transformation lineage needs to be built...</p>
                </div>
                {freshness && (
                  <div className={`inline-flex items-center gap-3 px-4 py-2.5 rounded-xl border mt-2 ${freshness.exists && !freshness.is_stale ? 'bg-emerald-950/50 border-emerald-700/50' : 'bg-amber-950/50 border-amber-700/50'}`}>
                    <span className={`text-xs font-bold ${freshness.exists && !freshness.is_stale ? 'text-emerald-400' : 'text-amber-400'}`}>
                      {freshness.exists ? (freshness.is_stale ? 'STALE — rebuilding...' : 'FRESH — loading graph...') : 'NOT BUILT — building now...'}
                    </span>
                    <span className="text-xs text-slate-400">{freshness.edge_count} edges · {freshness.age_str}</span>
                  </div>
                )}
              </div>
            )}

            {panelState === 'needs_build' && (
              <div className="flex flex-col items-center justify-center h-full gap-5 px-8 text-center">
                <div className="w-14 h-14 rounded-2xl bg-gradient-to-br from-purple-600/30 to-indigo-600/30 border border-purple-700/40 flex items-center justify-center">
                  <GitBranch size={24} className="text-purple-300" />
                </div>
                <div>
                  <h4 className="text-sm font-semibold text-slate-200 mb-2">
                    {freshness?.exists && freshness.is_stale
                      ? 'Transformation lineage may be out of date'
                      : 'Transformation lineage not generated yet'}
                  </h4>
                  <p className="text-xs text-slate-400 max-w-md leading-relaxed">
                    The column lineage for{' '}
                    <span className="text-purple-300 font-mono font-semibold">{selectedColumn}</span>{' '}
                    is already shown on the graph. Generating <span className="text-slate-200">transformation lineage</span>{' '}
                    parses the source pipeline to extract the exact SQL logic behind each
                    column of <span className="font-mono text-slate-300">{selectedTable?.split('.').slice(-1)[0]}</span> — useful
                    if you need to understand precisely how a column is derived.
                  </p>
                </div>

                {capturedExpressionCard && (
                  <div className="w-full max-w-md text-left">
                    {capturedExpressionCard}
                  </div>
                )}

                {/* Cost warning */}
                <div className="flex items-start gap-2.5 px-4 py-3 rounded-xl bg-amber-950/40 border border-amber-700/40 max-w-md text-left">
                  <AlertCircle size={16} className="text-amber-400 mt-0.5 shrink-0" />
                  <p className="text-[11px] text-amber-200/90 leading-relaxed">
                    <span className="font-semibold">Compute cost applies.</span> Generating runs a
                    serverless job that parses the producing pipeline and may take a few minutes.
                    It builds the logic for the whole table (all columns), then caches it — so this
                    only needs to run once per table. Generate only if you need the transformation detail.
                  </p>
                </div>

                {freshness?.exists && (
                  <p className="text-[10px] text-slate-500">
                    Existing version: {freshness.edge_count} edges · {freshness.age_str}
                  </p>
                )}

                <button
                  onClick={() => selectedTable && triggerBuild(selectedTable, !!(freshness?.exists && freshness.is_stale))}
                  className="inline-flex items-center gap-2 px-5 py-2.5 rounded-xl bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-500 hover:to-indigo-500 text-white text-xs font-semibold shadow-lg shadow-purple-900/40 transition-colors"
                >
                  <Zap size={14} />
                  {freshness?.exists && freshness.is_stale ? 'Regenerate transformation lineage' : 'Generate transformation lineage'}
                </button>

                <button
                  onClick={closePanel}
                  className="text-[11px] text-slate-500 hover:text-slate-300 transition-colors"
                >
                  No thanks — just show column lineage
                </button>
              </div>
            )}

            {panelState === 'building' && <BuildProgress status={buildStatus} />}

            {panelState === 'ready' && traceResult && (
              <div className="space-y-4">
                <div className="flex items-center gap-4 text-xs text-slate-400">
                  <span className="flex items-center gap-1.5"><Layers size={12} className="text-purple-400" />{traceResult.total_nodes} columns</span>
                  <span>·</span>
                  <span>{traceResult.total_edges} transforms</span>
                  <span>·</span>
                  <span>{traceResult.max_depth_reached} layers deep</span>
                  {traceResult.fetch_duration_ms != null && (<><span>·</span><span>{traceResult.fetch_duration_ms}ms</span></>)}
                  {traceResult.cached && (<span className="px-1.5 py-0.5 rounded bg-slate-800 text-slate-500 text-[10px]">cached</span>)}
                </div>

                {capturedExpressionCard}

                {!traceResult.is_source_column && traceResult.has_lineage && (
                  <p className="text-[10px] text-slate-600 italic">Click any upstream node to isolate its path to target. Use controls above to filter by depth or category.</p>
                )}

                {traceResult.is_source_column && (
                  <div className="text-center py-12">
                    <div className="w-12 h-12 rounded-full bg-slate-800 flex items-center justify-center mx-auto mb-4"><Layers size={20} className="text-slate-500" /></div>
                    <h4 className="text-sm font-semibold text-slate-300 mb-1">Source Column</h4>
                    <p className="text-xs text-slate-500"><span className="text-purple-400 font-mono font-bold">{selectedColumn}</span> has no upstream transformations — it originates here.</p>
                  </div>
                )}

                {!traceResult.is_source_column && traceResult.has_lineage && (
                  <>
                    <TransformCanvas
                      data={traceResult}
                      height={Math.min(
                        Math.max(340, traceResult.max_depth_reached * 130 + 160),
                        Math.round(window.innerHeight * 0.7),
                      )}
                    />
                    <div className="flex flex-wrap gap-3 pt-3 border-t border-slate-800">
                      {traceResult.levels.slice(0, 6).map((level) => (
                        <div key={level.depth} className="flex items-center gap-1.5">
                          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: level.color }} />
                          <span className="text-[10px] text-slate-400 font-medium">{level.label}</span>
                        </div>
                      ))}
                    </div>
                  </>
                )}

                {!traceResult.is_source_column && !traceResult.has_lineage && (
                  <div className="text-center py-12">
                    <div className="w-12 h-12 rounded-full bg-amber-950/40 flex items-center justify-center mx-auto mb-4"><AlertCircle size={20} className="text-amber-400" /></div>
                    <h4 className="text-sm font-semibold text-slate-300 mb-1">No transformation logic found</h4>
                    <p className="text-xs text-slate-500 max-w-sm mx-auto leading-relaxed">
                      No column-level transformation lineage was found for{' '}
                      <span className="text-purple-400 font-mono font-bold">{selectedColumn}</span>. Its
                      producer may be an <span className="text-slate-300">external or shared source</span>{' '}
                      (Delta Sharing / Lakehouse Federation), an unsupported producer type, or the build
                      genuinely found no transformation for this column.
                    </p>
                  </div>
                )}
              </div>
            )}

            {panelState === 'error' && (
              <div className="flex flex-col items-center justify-center h-full gap-4 px-8">
                <div className="w-12 h-12 rounded-full bg-red-950/50 flex items-center justify-center"><AlertCircle size={20} className="text-red-400" /></div>
                <div className="text-center">
                  <h4 className="text-sm font-semibold text-red-300 mb-2">Transformation Lineage Error</h4>
                  <p className="text-xs text-slate-400 max-w-sm">{panelError || 'An unexpected error occurred.'}</p>
                </div>
                <button onClick={closePanel} className="mt-2 px-4 py-2 rounded-lg border border-slate-700 text-slate-300 text-xs font-medium hover:bg-slate-800 transition-colors">Dismiss</button>
              </div>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
