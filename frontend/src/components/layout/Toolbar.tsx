import { memo, useCallback, useEffect, useState, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { GitBranch, Search, ChevronDown, Columns3, Zap, Info, Lock, AlertTriangle, ArrowLeft, Percent, FolderTree, Layers, Download } from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { api, setLiveMode } from "../../api/client";
import { goLanding, goSchemas, goCatalogs } from "../../hooks/useRouter";
import { exportLineageToExcel } from "../../lib/exportLineage";
import HeaderMenu from "./HeaderMenu";

interface Props {
  onGenerate: () => void;
}

function Toolbar({ onGenerate }: Props) {
  const {
    catalog, schema, focusTable, scope, lineageView, lineageDepth, columnLineageEnabled, liveMode, isAdmin,
    catalogs, schemas, loading, cached, cachedAt, cacheExpiresAt, fetchDurationMs,
    setCatalog, setSchema, setFocusTable, setLineageView, setLineageDepth, setColumnLineageEnabled, setLiveMode: setStoreLiveMode,
    setCatalogs, setSchemas, setSearchOpen, discountPercent, setDiscountPercent,
  } = useLineageStore();

  const nodes = useLineageStore((s) => s.nodes);
  const [toast, setToast] = useState<string | null>(null);
  const orphanCount = useMemo(() => nodes.filter((n) => n.node_type === "table" && n.lineage_status === "orphan").length, [nodes]);

  // Whole-schema / whole-catalog lineage: no focused table, but a scope is active.
  const isScopeLineage = !focusTable && (scope === "schema" || scope === "catalog");
  // Column lineage is per-schema; catalog-wide scope has no single schema to trace.
  const columnsDisabled = lineageView === "pipeline" || (isScopeLineage && scope === "catalog");

  const handleLiveModeToggle = useCallback(() => {
    if (!isAdmin) {
      setToast("Only workspace admins can enable live mode.");
      return;
    }
    const next = !liveMode;
    setStoreLiveMode(next);
    setLiveMode(next);
    if (next) {
      setToast("Live mode enabled — next refresh will query system tables directly. This may take a few seconds.");
    } else {
      setToast("Live mode disabled — data will be served from cache for faster loading.");
    }
  }, [liveMode, isAdmin, setStoreLiveMode]);

  // Auto-dismiss toast
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 4000);
    return () => clearTimeout(t);
  }, [toast]);

  useEffect(() => {
    api.getCatalogs().then((r) => setCatalogs(r.catalogs)).catch(console.error);
  }, [setCatalogs]);

  useEffect(() => {
    if (!catalog) return;
    let cancelled = false;
    api.getSchemas(catalog).then((r) => {
      if (!cancelled) setSchemas(r.schemas);
    }).catch((e) => {
      if (!cancelled) console.error(e);
    });
    return () => { cancelled = true; };
  }, [catalog, setSchemas]);

  const handleGenerate = useCallback(() => {
    if (catalog && schema) onGenerate();
  }, [catalog, schema, onGenerate]);

  // Export the currently rendered graph to a multi-sheet .xlsx (client-side).
  const handleExport = useCallback(() => {
    const s = useLineageStore.getState();
    if (!s.nodes.length) {
      setToast("Nothing to export yet — generate a lineage graph first.");
      return;
    }
    try {
      exportLineageToExcel({
        nodes: s.nodes,
        edges: s.edges,
        columnEdges: s.columnEdges,
        scope: s.scope,
        catalog: s.catalog,
        schema: s.schema,
        focusTable: s.focusTable,
      });
    } catch (e) {
      console.error("Export failed:", e);
      setToast("Export failed — see console for details.");
    }
  }, []);

  return (
    <>
    <motion.header
      initial={{ y: -10, opacity: 0 }}
      animate={{ y: 0, opacity: 1 }}
      transition={{ duration: 0.25 }}
      className="
        relative z-50 flex items-center gap-4 px-5 h-14
        bg-[#0D0D16]/90 backdrop-blur-xl
        border-b border-white/[0.04]
      "
    >
      {/* Logo — clickable, returns to home */}
      <button
        onClick={goLanding}
        className="flex items-center gap-2.5 mr-1 hover:opacity-90 transition-opacity"
        title="Back to home"
        aria-label="Back to home"
      >
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent to-purple-500 flex items-center justify-center shadow-[0_0_12px_rgba(99,102,241,0.3)]">
          <GitBranch size={16} className="text-white" />
        </div>
        <div className="text-left">
          <div className="font-semibold text-[14px] text-white tracking-tight leading-none">
            Lineage Explorer
          </div>
          <div className="text-[9px] text-slate-600 tracking-wider uppercase mt-0.5">
            Unity Catalog
          </div>
        </div>
      </button>

      {/* Divider */}
      <div className="w-px h-8 bg-white/[0.06]" />

      {/* Back button + focused table */}
      {focusTable ? (
        <>
          <button
            onClick={() => setFocusTable(null)}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.06] hover:border-white/[0.12] transition-all duration-200 group"
            title="Back to search"
          >
            <ArrowLeft size={13} className="text-slate-500 group-hover:text-slate-300 transition-colors" />
            <span className="text-[11px] text-slate-500 group-hover:text-slate-300 font-medium transition-colors">Back</span>
          </button>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent/[0.06] border border-accent/20">
            <div className="w-1.5 h-1.5 rounded-full bg-accent shadow-[0_0_6px] shadow-accent/40" />
            <span className="font-mono text-[12px] text-accent-light tracking-tight">{focusTable}</span>
          </div>
        </>
      ) : isScopeLineage ? (
        <>
          <button
            onClick={() => (scope === "catalog" ? goCatalogs() : goSchemas(catalog))}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.06] hover:border-white/[0.12] transition-all duration-200 group"
            title="Back to browse"
          >
            <ArrowLeft size={13} className="text-slate-500 group-hover:text-slate-300 transition-colors" />
            <span className="text-[11px] text-slate-500 group-hover:text-slate-300 font-medium transition-colors">Back</span>
          </button>
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-accent/[0.06] border border-accent/20">
            {scope === "catalog" ? <FolderTree size={13} className="text-accent-light" /> : <Layers size={13} className="text-accent-light" />}
            <span className="text-[9px] font-semibold uppercase tracking-wider text-accent-light/70">
              {scope === "catalog" ? "Catalog" : "Schema"}
            </span>
            <span className="font-mono text-[12px] text-accent-light tracking-tight">
              {scope === "catalog" ? catalog : `${catalog}.${schema}`}
            </span>
          </div>
        </>
      ) : (
        <>
          {/* Catalog */}
          <SelectBox
            label="Catalog"
            value={catalog}
            options={catalogs}
            onChange={setCatalog}
            placeholder="Select catalog"
          />

          {/* Schema */}
          <SelectBox
            label="Schema"
            value={schema}
            options={schemas}
            onChange={setSchema}
            placeholder="Select schema"
            disabled={!catalog}
          />
        </>
      )}

      {/* View Mode — three-segment slider */}
      <div className="flex items-center gap-1.5 px-1 py-1 rounded-xl bg-white/[0.02] border border-white/[0.04]">
        {(["pipeline", "table", "full"] as const).map((mode) => {
          const isActive = lineageView === mode;
          const labels = { pipeline: "Pipelines", table: "Tables", full: "Full" };
          return (
            <button
              key={mode}
              onClick={() => setLineageView(mode)}
              className={`
                relative px-3 py-1 rounded-lg text-[10px] font-semibold tracking-wide uppercase
                transition-all duration-300 ease-out
                ${isActive
                  ? "bg-gradient-to-r from-accent/20 to-purple-500/20 text-accent-light shadow-[0_0_8px_rgba(99,102,241,0.15)] border border-accent/30"
                  : "text-slate-600 hover:text-slate-400 border border-transparent"
                }
              `}
            >
              {labels[mode]}
            </button>
          );
        })}
      </div>

      {/* Depth control */}
      {focusTable && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.04]">
          <span className="text-[11px] text-slate-500 font-medium">Depth</span>
          <input
            type="text"
            inputMode="numeric"
            maxLength={2}
            value={lineageDepth || ""}
            onChange={(e) => {
              const raw = e.target.value.replace(/\D/g, "").slice(0, 2);
              setLineageDepth(raw ? parseInt(raw, 10) : 0);
            }}
            placeholder="All"
            className="w-10 bg-white/[0.04] border border-white/[0.06] rounded-md px-2 py-0.5 text-[11px] font-mono text-slate-300 placeholder:text-slate-600 outline-none focus:border-accent/40 text-center"
          />
        </div>
      )}

      {/* Discount — applies to serverless job costs on pipeline nodes */}
      {focusTable && (
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.04]">
          <Percent size={13} className="text-slate-500" />
          <span className="text-[11px] text-slate-500 font-medium">Discount</span>
          <input
            type="text"
            inputMode="numeric"
            maxLength={2}
            value={discountPercent || ""}
            onChange={(e) => {
              const raw = e.target.value.replace(/\D/g, "").slice(0, 2);
              setDiscountPercent(raw ? parseInt(raw, 10) : 0);
            }}
            placeholder="0"
            className="w-8 bg-white/[0.04] border border-white/[0.06] rounded-md px-2 py-0.5 text-[11px] font-mono text-slate-300 placeholder:text-slate-600 outline-none focus:border-accent/40 text-center"
          />
          <span className="text-[11px] text-slate-600">%</span>
        </div>
      )}

      {/* Column Lineage — disabled in pipeline-only mode and catalog-wide scope */}
      <div className={`flex items-center gap-2.5 px-3 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.04] ${columnsDisabled ? "opacity-30 pointer-events-none" : ""}`}>
        <Columns3 size={13} className="text-slate-500" />
        <span className="text-[11px] text-slate-500 font-medium">Columns</span>
        <button
          onClick={() => !columnsDisabled && setColumnLineageEnabled(!columnLineageEnabled)}
          disabled={columnsDisabled}
          className={`
            relative w-8 h-[18px] rounded-full transition-all duration-300
            ${columnsDisabled ? "cursor-not-allowed" : ""}
            ${columnLineageEnabled && !columnsDisabled
              ? "bg-gradient-to-r from-accent to-purple-500 shadow-[0_0_10px_rgba(99,102,241,0.3)]"
              : "bg-white/[0.06]"
            }
          `}
        >
          <motion.div
            animate={{ x: columnLineageEnabled ? 15 : 2 }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
            className="absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow-sm"
          />
        </button>
      </div>

      {/* Live Query */}
      <div
        className={`flex items-center gap-2.5 px-3 py-1.5 rounded-lg bg-white/[0.02] border border-white/[0.04] ${!isAdmin ? "opacity-50" : ""}`}
        title={!isAdmin ? "Only workspace admins can enable live mode" : "Toggle live query mode"}
      >
        <Zap size={13} className={liveMode ? "text-amber-400" : "text-slate-500"} />
        <span className="text-[11px] text-slate-500 font-medium">Live</span>
        {!isAdmin && <Lock size={10} className="text-slate-600" />}
        <button
          onClick={handleLiveModeToggle}
          disabled={!isAdmin}
          className={`
            relative w-8 h-[18px] rounded-full transition-all duration-300
            ${!isAdmin ? "cursor-not-allowed" : ""}
            ${liveMode
              ? "bg-gradient-to-r from-amber-500 to-orange-500 shadow-[0_0_10px_rgba(245,158,11,0.3)]"
              : "bg-white/[0.06]"
            }
          `}
        >
          <motion.div
            animate={{ x: liveMode ? 15 : 2 }}
            transition={{ type: "spring", stiffness: 500, damping: 30 }}
            className="absolute top-[2px] w-[14px] h-[14px] rounded-full bg-white shadow-sm"
          />
        </button>
      </div>

      {/* Generate — only shown when using catalog/schema dropdowns (no focusTable, no active scope) */}
      {!focusTable && !isScopeLineage && (
        <button
          onClick={handleGenerate}
          disabled={!catalog || !schema || loading}
          className={`
            relative px-5 py-2 rounded-xl text-[13px] font-semibold transition-all duration-300
            ${catalog && schema && !loading
              ? "bg-gradient-to-r from-accent to-purple-500 text-white shadow-[0_0_20px_rgba(99,102,241,0.2)] hover:shadow-[0_0_30px_rgba(99,102,241,0.35)] active:scale-[0.97]"
              : "bg-white/[0.04] text-slate-600 cursor-not-allowed"
            }
          `}
        >
          {loading ? (
            <motion.span
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ duration: 1.2, repeat: Infinity }}
            >
              Loading...
            </motion.span>
          ) : (
            "Generate Lineage"
          )}
        </button>
      )}

      {/* Spacer */}
      <div className="flex-1" />

      {/* Export to Excel — only when a graph is loaded */}
      {nodes.length > 0 && (
        <button
          onClick={handleExport}
          className="flex items-center gap-1.5 px-3 py-1.5 flex-shrink-0 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/25 text-emerald-300 hover:text-emerald-200 text-[11px] font-medium transition-all duration-200"
          title="Export this lineage to an Excel file"
        >
          <Download size={13} />
          Export
        </button>
      )}

      {/* Search — compact icon button */}
      <button
        onClick={() => setSearchOpen(true)}
        className="flex items-center justify-center w-8 h-8 flex-shrink-0 rounded-lg bg-white/[0.03] hover:bg-white/[0.06] border border-white/[0.04] hover:border-white/[0.08] transition-all duration-200"
        title="Search (Cmd+K)"
      >
        <Search size={14} className="text-slate-500" />
      </button>

      {/* Shared menu */}
      <HeaderMenu />
    </motion.header>
    {/* Cache status banner */}
    {nodes.length > 0 && (
      <div className={`
        flex items-center justify-center gap-2 px-4 py-1 text-[10px] font-medium tracking-wide
        ${liveMode
          ? "bg-amber-500/10 text-amber-400 border-b border-amber-500/20"
          : "bg-white/[0.02] text-slate-600 border-b border-white/[0.04]"
        }
      `}>
        {/* Fetch duration badge */}
        {fetchDurationMs != null && (
          <span className={`px-1.5 py-0.5 rounded text-[9px] font-mono ${
            fetchDurationMs === 0
              ? "bg-emerald-500/15 text-emerald-400"
              : fetchDurationMs < 3000
                ? "bg-blue-500/15 text-blue-400"
                : "bg-amber-500/15 text-amber-400"
          }`}>
            {fetchDurationMs === 0 ? "cache <1ms" : `${(fetchDurationMs / 1000).toFixed(1)}s`}
          </span>
        )}
        {liveMode ? (
          <>
            <span className="relative flex h-1.5 w-1.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-amber-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-1.5 w-1.5 bg-amber-400" />
            </span>
            LIVE MODE — Fetching fresh data from Unity Catalog system tables
          </>
        ) : cached ? (
          <>
            <Info size={10} className="text-slate-600 flex-shrink-0" />
            Cached{cachedAt ? ` · Refreshed ${formatTimeAgo(cachedAt)}` : ""}
            {cacheExpiresAt ? ` · Expires ${formatTimeUntil(cacheExpiresAt)}` : ""}
            {isAdmin && (
              <>
                {" · "}
                <button onClick={handleLiveModeToggle} className="underline underline-offset-2 hover:text-slate-400 transition-colors">
                  Enable live mode for latest data
                </button>
              </>
            )}
          </>
        ) : (
          <>Loaded fresh from system tables{cacheExpiresAt ? ` · Cache expires ${formatTimeUntil(cacheExpiresAt)}` : ""}</>
        )}
      </div>
    )}

    {/* Orphan tables banner — hidden in focused table mode */}
    {orphanCount > 0 && !focusTable && (
      <div className="flex items-center justify-center gap-2 px-4 py-1 text-[10px] font-medium tracking-wide bg-amber-500/5 text-amber-400/80 border-b border-amber-500/10">
        <AlertTriangle size={10} className="flex-shrink-0" />
        {orphanCount} {orphanCount === 1 ? "table has" : "tables have"} no lineage recorded — no tracked query has read from or written to {orphanCount === 1 ? "it" : "them"}.
        {" "}
        <a
          href="https://docs.databricks.com/aws/en/data-governance/unity-catalog/data-lineage"
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 hover:text-amber-300 transition-colors"
        >
          UC lineage limitations
        </a>
      </div>
    )}

    {/* Toast notification for live mode toggle */}
    <AnimatePresence>
      {toast && (
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20 }}
          transition={{ duration: 0.3 }}
          className="fixed top-20 left-1/2 -translate-x-1/2 z-[100] flex items-center gap-2.5 px-4 py-2.5 rounded-xl bg-[#1A1A2E]/95 backdrop-blur-md border border-white/[0.08] shadow-[0_8px_32px_rgba(0,0,0,0.5)]"
        >
          <Zap size={13} className={liveMode ? "text-amber-400" : "text-slate-500"} />
          <span className="text-[12px] text-slate-300 font-medium max-w-[360px]">{toast}</span>
          <button
            onClick={() => setToast(null)}
            className="text-slate-600 hover:text-slate-400 text-[14px] ml-1 transition-colors"
          >
            &times;
          </button>
        </motion.div>
      )}
    </AnimatePresence>

    </>
  );
}

function formatTimeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ${mins % 60}m ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function formatTimeUntil(isoDate: string): string {
  const diff = new Date(isoDate).getTime() - Date.now();
  if (diff <= 0) return "expired";
  const mins = Math.floor(diff / 60000);
  if (mins < 60) return `in ${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `in ${hrs}h ${mins % 60}m`;
  return `in ${Math.floor(hrs / 24)}d`;
}

function SelectBox({
  label,
  value,
  options,
  onChange,
  placeholder,
  disabled = false,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  placeholder: string;
  disabled?: boolean;
}) {
  return (
    <div className="relative">
      <label className="absolute -top-1 left-3 text-[8px] text-slate-600 uppercase tracking-[0.1em] font-semibold bg-[#0D0D16] px-1 z-10">
        {label}
      </label>
      <div className="relative">
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          disabled={disabled}
          className={`
            appearance-none bg-white/[0.02] border border-white/[0.06]
            rounded-lg px-3.5 py-2 pr-8 text-[12px] font-mono
            min-w-[190px] outline-none
            transition-all duration-200
            ${disabled ? "opacity-30 cursor-not-allowed" : "hover:border-white/[0.12] focus:border-accent/40 focus:shadow-[0_0_12px_rgba(99,102,241,0.1)] cursor-pointer"}
            ${value ? "text-slate-100" : "text-slate-600"}
          `}
        >
          <option value="" disabled>{placeholder}</option>
          {options.map((opt) => (
            <option key={opt} value={opt} className="bg-[#14141F] text-slate-200">
              {opt}
            </option>
          ))}
        </select>
        <ChevronDown size={12} className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-600 pointer-events-none" />
      </div>
    </div>
  );
}

export default memo(Toolbar);
