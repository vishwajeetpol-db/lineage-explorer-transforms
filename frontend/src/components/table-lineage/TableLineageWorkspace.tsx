import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ReactFlowProvider } from "reactflow";
import {
  ArrowLeft, GitBranch, ShieldAlert, Stethoscope, ScrollText,
  KeyRound, Boxes, Sparkles, Loader2, ArrowUpFromLine, ArrowDownToLine, Columns3, Clock,
} from "lucide-react";
import LineageCanvas from "../graph/LineageCanvas";
import { useLineageStore } from "../../store/lineageStore";
import { api, setLiveMode } from "../../api/client";
import { goLanding, goTableLineage } from "../../hooks/useRouter";
import CatalogTreePanel from "./CatalogTreePanel";
import DraggablePanel from "./DraggablePanel";
import ThemeToggle from "../ui/ThemeToggle";
import ImpactPanel from "./ImpactPanel";
import GovernancePanel from "./GovernancePanel";
import AccessPanel from "./AccessPanel";
import MLModelsPanel from "./MLModelsPanel";
import ColumnTransformationPanel from "./ColumnTransformationPanel";
import RootCausePanel from "./RootCausePanel";

type TabKey = "impact" | "rootCause" | "governance" | "access" | "ml" | "llm";

const TABS: { key: TabKey; label: string; icon: typeof GitBranch; accent: string }[] = [
  { key: "impact", label: "Impact", icon: ShieldAlert, accent: "text-rose-400" },
  { key: "rootCause", label: "Root Cause", icon: Stethoscope, accent: "text-amber-400" },
  { key: "governance", label: "Governance", icon: ScrollText, accent: "text-emerald-400" },
  { key: "access", label: "Access", icon: KeyRound, accent: "text-sky-400" },
  { key: "ml", label: "ML Models", icon: Boxes, accent: "text-cyan-400" },
  { key: "llm", label: "Column Transformation", icon: Sparkles, accent: "text-violet-400" },
];

const PANEL_TITLE: Record<TabKey, string> = {
  impact: "Impact analysis",
  rootCause: "Root-cause trace",
  governance: "Governance",
  access: "Access & security",
  ml: "ML models",
  llm: "Column Transformation Lineage",
};

function renderPanelBody(key: TabKey, table: string | null) {
  switch (key) {
    case "impact": return <ImpactPanel table={table} />;
    case "rootCause": return <RootCausePanel table={table} />;
    case "governance": return <GovernancePanel table={table} />;
    case "access": return <AccessPanel table={table} />;
    case "ml": return <MLModelsPanel table={table} />;
    case "llm": return <ColumnTransformationPanel table={table} />;
  }
}

function StatBlock({ icon: Icon, label, value }: { icon: typeof Columns3; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <Icon size={15} className="text-slate-500 shrink-0" />
      <div className="leading-tight">
        <div className="text-[15px] font-semibold text-slate-100">{value}</div>
        <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      </div>
    </div>
  );
}

export default function TableLineageWorkspace({ initialTable }: { initialTable?: string }) {
  const [selected, setSelected] = useState<string | null>(initialTable ?? null);
  // Open draggable panels (in stacking order — last is on top).
  const [openPanels, setOpenPanels] = useState<TabKey[]>([]);
  const loading = useLineageStore((s) => s.loading);
  const nodes = useLineageStore((s) => s.nodes);
  const abortRef = useRef<AbortController | null>(null);

  // Summary stats for the selected table, from the focus node in the graph.
  const summary = useMemo(() => {
    if (!selected) return null;
    const n = nodes.find((x): x is Extract<typeof x, { node_type: "table" }> =>
      x.node_type === "table" && (x as any).full_name === selected);
    if (!n) return null;
    return {
      name: n.name,
      type: n.table_type,
      comment: n.comment,
      updated: n.updated_at,
      columns: (n.columns || []).length,
      upstream: n.upstream_count,
      downstream: n.downstream_count,
    };
  }, [selected, nodes]);

  const loadTrace = useCallback(async (table: string) => {
    setLiveMode(useLineageStore.getState().liveMode);
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    useLineageStore.getState().setFocusTable(table);
    useLineageStore.setState({ loading: true, error: null });
    try {
      const data = await api.getLineageTrace(table, controller.signal);
      if (controller.signal.aborted) return;
      useLineageStore.getState().setLineageData({
        nodes: data.nodes,
        edges: data.edges,
        cached: data.cached,
        cachedAt: data.cached_at,
        cacheExpiresAt: data.cache_expires_at,
        fetchDurationMs: data.fetch_duration_ms,
        lineageWindowDays: data.lineage_window_days,
        truncated: data.truncated,
        graphWarnings: data.graph_warnings ?? null,
      });
      const store = useLineageStore.getState();
      store.setColumnLineageEnabled(true);
      useLineageStore.setState({ expandedNodes: new Set([table]), selectedNode: null });
    } catch (err: any) {
      if (err?.name === "AbortError") return;
      useLineageStore.getState().setError(err.message || "Failed to load lineage");
    }
  }, []);

  useEffect(() => {
    if (selected) loadTrace(selected);
    return () => abortRef.current?.abort();
  }, [selected, loadTrace]);

  // Double-click a table node in the graph re-focuses the workspace.
  const selectedNode = useLineageStore((s) => s.selectedNode);
  useEffect(() => {
    if (
      selectedNode &&
      selectedNode !== selected &&
      selectedNode.split(".").length === 3 &&
      !selectedNode.startsWith("entity:") &&
      !selectedNode.startsWith("path:")
    ) {
      setSelected(selectedNode);
      goTableLineage(selectedNode);
    }
  }, [selectedNode, selected]);

  const handleSelect = (fqn: string) => {
    setSelected(fqn);
    goTableLineage(fqn);
  };

  // Toggle a panel open/closed; opening (or re-clicking) brings it to the front.
  const togglePanel = (key: TabKey) => {
    setOpenPanels((cur) => {
      if (cur.includes(key)) {
        // Already open → if it's already on top, close it; else bring to front.
        if (cur[cur.length - 1] === key) return cur.filter((k) => k !== key);
        return [...cur.filter((k) => k !== key), key];
      }
      return [...cur, key];
    });
  };
  const bringToFront = (key: TabKey) =>
    setOpenPanels((cur) => (cur[cur.length - 1] === key ? cur : [...cur.filter((k) => k !== key), key]));
  const closePanel = (key: TabKey) => setOpenPanels((cur) => cur.filter((k) => k !== key));

  return (
    <div className="h-screen w-screen flex flex-col bg-surface overflow-hidden">
      {/* Header bar */}
      <div className="flex items-center gap-3 px-4 h-11 border-b border-white/[0.06] shrink-0">
        <button onClick={goLanding} className="flex items-center gap-1.5 text-[12px] text-slate-400 hover:text-accent-light transition-colors">
          <ArrowLeft size={14} /> Home
        </button>
        <div className="w-px h-4 bg-white/[0.08]" />
        <span className="w-8 h-8 rounded-lg overflow-hidden inline-flex items-center justify-center shrink-0">
          <img src="/bricktrace-logo.png" alt="" className="w-full h-full object-contain" />
        </span>
        <span className="text-[13px] font-semibold text-slate-100">Table Lineage</span>
        {selected && <span className="font-mono text-[11px] text-slate-500 truncate ml-1">· {selected}</span>}
        {loading && <Loader2 size={13} className="animate-spin text-accent ml-1" />}
        <div className="ml-auto"><ThemeToggle /></div>
      </div>

      {/* Body: left tree + right (summary bar over graph) */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left: catalog tree */}
        <div className="w-[260px] shrink-0 border-r border-white/[0.06] bg-surface-50/40">
          <CatalogTreePanel selected={selected} onSelect={handleSelect} />
        </div>

        {/* Right column: summary bar + graph */}
        <div className="flex-1 flex flex-col min-w-0 relative">
          {/* Top summary bar */}
          {selected && (
            <div className="shrink-0 border-b border-white/[0.06] bg-surface-50/40 px-5 py-3 flex items-center gap-6">
              {/* Table identity */}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[15px] font-semibold text-slate-100 truncate">{summary?.name || selected.split(".").pop()}</span>
                  {summary?.type && (
                    <span className="text-[9px] font-bold tracking-wider px-1.5 py-0.5 rounded-full bg-amber-500/10 text-amber-300 border border-amber-500/25 uppercase shrink-0">
                      {summary.type.replace("_", " ")}
                    </span>
                  )}
                </div>
                <div className="font-mono text-[11px] text-slate-500 truncate">{selected}</div>
                {summary?.comment && <div className="text-[11px] text-slate-400 truncate max-w-[400px]">{summary.comment}</div>}
              </div>

              {/* Stats */}
              {summary && (
                <div className="flex items-center gap-6 shrink-0">
                  <StatBlock icon={Columns3} label="Columns" value={summary.columns} />
                  <StatBlock icon={ArrowUpFromLine} label="Upstream" value={summary.upstream} />
                  <StatBlock icon={ArrowDownToLine} label="Downstream" value={summary.downstream} />
                  {summary.updated && <StatBlock icon={Clock} label="Updated" value={String(summary.updated).slice(0, 10)} />}
                </div>
              )}

              {/* Capability buttons (right) */}
              <div className="flex flex-wrap items-center gap-1.5 ml-auto justify-end">
                {TABS.map((t) => {
                  const Icon = t.icon;
                  const open = openPanels.includes(t.key);
                  return (
                    <button
                      key={t.key}
                      onClick={() => togglePanel(t.key)}
                      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-[11px] font-medium transition-all border ${
                        open
                          ? "bg-surface-200 text-slate-100 border-white/[0.15]"
                          : "bg-surface-100/50 text-slate-400 hover:text-slate-200 border-white/[0.06] hover:border-white/[0.12]"
                      }`}
                    >
                      <Icon size={12} className={t.accent} />
                      {t.label}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {/* Center: lineage graph */}
          <div className="flex-1 relative min-w-0">
            {selected ? (
              <ReactFlowProvider>
                <LineageCanvas />
              </ReactFlowProvider>
            ) : (
              <div className="h-full flex flex-col items-center justify-center gap-3 text-slate-600">
                <GitBranch size={28} className="text-slate-700" />
                <span className="text-[13px]">Select a table from the tree to trace its lineage.</span>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Draggable capability panels */}
      {openPanels.map((key, idx) => (
        <DraggablePanel
          key={key}
          title={
            <span className="flex items-center gap-2">
              {(() => { const T = TABS.find((t) => t.key === key)!; const I = T.icon; return <I size={14} className={T.accent} />; })()}
              {PANEL_TITLE[key]}
            </span>
          }
          subtitle={selected || undefined}
          initial={{ x: window.innerWidth - 380 - 32 - idx * 28, y: 120 + idx * 28 }}
          z={40 + idx}
          onFocus={() => bringToFront(key)}
          onClose={() => closePanel(key)}
        >
          {renderPanelBody(key, selected)}
        </DraggablePanel>
      ))}
    </div>
  );
}
