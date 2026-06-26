import { memo, useEffect, useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Activity, Database, Clock, Cpu, HardDrive, Zap, Users, Layers, AlertTriangle, RefreshCw, Trash2 } from "lucide-react";
import { api } from "../api/client";
import type { AdminStatus } from "../api/client";

interface Props {
  open: boolean;
  onClose: () => void;
}

function MetricCard({ label, value, sub, icon: Icon, color }: { label: string; value: string | number; sub?: string; icon: typeof Activity; color: string }) {
  return (
    <div className="bg-black/40 border border-emerald-500/10 rounded-lg p-3 backdrop-blur-sm">
      <div className="flex items-center gap-2 mb-1.5">
        <Icon size={12} className={color} />
        <span className="text-[9px] uppercase tracking-[0.15em] text-emerald-500/60 font-mono">{label}</span>
      </div>
      <div className={`text-[22px] font-mono font-bold ${color} leading-none`}>{value}</div>
      {sub && <div className="text-[10px] font-mono text-emerald-500/40 mt-1">{sub}</div>}
    </div>
  );
}

function AdminDashboard({ open, onClose }: Props) {
  const [status, setStatus] = useState<AdminStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [txBusy, setTxBusy] = useState(false);
  const [txMsg, setTxMsg] = useState<string | null>(null);

  const invalidateTx = async (scope: "cache" | "all") => {
    if (scope === "all" && !window.confirm(
      "Wipe ALL stored transformation lineage?\n\nEvery table will show 'not built' until regenerated. The audit-path and LLM-expression caches are kept."
    )) return;
    setTxBusy(true); setTxMsg(null);
    try {
      const r = await api.invalidateTransform(scope);
      setTxMsg(scope === "all" ? `Wiped ${r.cleared?.length ?? 0} stored tables + flushed cache` : "In-memory caches flushed");
    } catch (e: any) {
      setTxMsg(`Error: ${e.message}`);
    } finally {
      setTxBusy(false);
    }
  };

  const fetchStatus = () => {
    setLoading(true);
    setError(null);
    api.getAdminStatus()
      .then((s) => { setStatus(s); setLoading(false); })
      .catch((e) => { setError(e.message); setLoading(false); });
  };

  useEffect(() => {
    if (!open) return;
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000); // auto-refresh every 10s
    return () => clearInterval(interval);
  }, [open]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/70 backdrop-blur-sm z-[9998]"
            onClick={onClose}
          />
          <motion.div
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.95, y: 20 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-4 z-[9999] flex items-start justify-center pt-8 pointer-events-none"
          >
            <div className="pointer-events-auto w-full max-w-[900px] max-h-[85vh] overflow-y-auto rounded-2xl bg-[#0a0f0a]/95 border border-emerald-500/20 shadow-[0_0_60px_rgba(16,185,129,0.1)] backdrop-blur-xl">
              {/* Header */}
              <div className="sticky top-0 z-10 flex items-center justify-between px-6 py-4 border-b border-emerald-500/10 bg-[#0a0f0a]/95 backdrop-blur-xl">
                <div className="flex items-center gap-3">
                  <div className="w-2 h-2 rounded-full bg-emerald-400 shadow-[0_0_8px] shadow-emerald-400/60 animate-pulse" />
                  <span className="font-mono text-[14px] text-emerald-400 font-bold tracking-wider">SYSTEM STATUS</span>
                  <span className="font-mono text-[10px] text-emerald-500/40">ADMIN ONLY</span>
                </div>
                <div className="flex items-center gap-3">
                  {txMsg && <span className="text-[10px] font-mono text-emerald-500/60 max-w-[260px] truncate">{txMsg}</span>}
                  <button
                    onClick={() => invalidateTx("cache")}
                    disabled={txBusy}
                    title="Flush in-memory transform caches (freshness/edges/trace). No data loss."
                    className="inline-flex items-center gap-1 px-2 py-1 rounded border border-emerald-500/20 text-[10px] font-mono text-emerald-400/80 hover:bg-emerald-500/10 disabled:opacity-40 transition-colors"
                  >
                    <RefreshCw size={11} className={txBusy ? "animate-spin" : ""} /> Flush cache
                  </button>
                  <button
                    onClick={() => invalidateTx("all")}
                    disabled={txBusy}
                    title="Wipe ALL stored transformation lineage (start fresh). Tables show 'not built' until regenerated."
                    className="inline-flex items-center gap-1 px-2 py-1 rounded border border-red-500/30 text-[10px] font-mono text-red-400/90 hover:bg-red-500/10 disabled:opacity-40 transition-colors"
                  >
                    <Trash2 size={11} /> Wipe lineage
                  </button>
                  {loading && <div className="w-3 h-3 border border-emerald-500/40 border-t-emerald-400 rounded-full animate-spin" />}
                  <button onClick={onClose} className="text-emerald-500/40 hover:text-emerald-400 transition-colors">
                    <X size={18} />
                  </button>
                </div>
              </div>

              {error && (
                <div className="px-6 py-3 text-[12px] font-mono text-red-400 bg-red-500/5 border-b border-red-500/10">
                  ERROR: {error}
                </div>
              )}

              {status && (
                <div className="p-6 space-y-6">
                  {/* Top metrics grid */}
                  <div className="grid grid-cols-4 gap-3">
                    <MetricCard
                      label="P99 Latency"
                      value={`${status.latency.p99_ms.toFixed(0)}ms`}
                      sub={`P50: ${status.latency.p50_ms.toFixed(0)}ms · P95: ${status.latency.p95_ms.toFixed(0)}ms`}
                      icon={Zap}
                      color={status.latency.p99_ms > 2000 ? "text-red-400" : status.latency.p99_ms > 500 ? "text-amber-400" : "text-emerald-400"}
                    />
                    <MetricCard
                      label="Memory RSS"
                      value={`${status.memory.rss_mb}MB`}
                      sub={`${status.memory.rss_percent}% of 6GB runtime`}
                      icon={HardDrive}
                      color={status.memory.rss_percent > 70 ? "text-red-400" : status.memory.rss_percent > 40 ? "text-amber-400" : "text-emerald-400"}
                    />
                    <MetricCard
                      label="Requests"
                      value={status.requests.total.toLocaleString()}
                      sub={`${status.requests.rate_per_min}/min current`}
                      icon={Activity}
                      color="text-emerald-400"
                    />
                    <MetricCard
                      label="Uptime"
                      value={status.system.uptime_human}
                      sub={`PID ${status.system.pid} · Python ${status.system.python_version}`}
                      icon={Clock}
                      color="text-emerald-400"
                    />
                  </div>

                  {/* Upgrade advisory */}
                  {(() => {
                    const reasons: string[] = [];
                    if (status.memory.rss_percent > 60) reasons.push(`Memory at ${status.memory.rss_percent}% (>60%)`);
                    if (status.latency.p99_ms > 2000) reasons.push(`P99 latency ${status.latency.p99_ms.toFixed(0)}ms (>2s)`);
                    if (status.cache.utilization_percent > 80) reasons.push(`Cache ${status.cache.utilization_percent}% full (>80%)`);
                    if (status.thread_pool.inflight_cache_keys.length > 6) reasons.push(`${status.thread_pool.inflight_cache_keys.length} inflight queries (>6)`);
                    if (reasons.length === 0) return null;
                    return (
                      <div className="bg-amber-500/[0.06] border border-amber-500/20 rounded-lg px-4 py-3">
                        <div className="flex items-center gap-2 mb-2">
                          <AlertTriangle size={13} className="text-amber-400" />
                          <span className="font-mono text-[11px] text-amber-400 font-bold tracking-wide">UPGRADE ADVISORY: Consider switching to Large app config</span>
                        </div>
                        <div className="space-y-1">
                          {reasons.map((r, i) => (
                            <div key={i} className="font-mono text-[10px] text-amber-400/70 flex items-center gap-2">
                              <span className="w-1 h-1 rounded-full bg-amber-400/50 flex-shrink-0" />
                              {r}
                            </div>
                          ))}
                        </div>
                        <div className="mt-2 font-mono text-[9px] text-amber-500/50">
                          Large config: 8 CPU, 16GB RAM. Update compute_size in databricks.yml or via the Databricks Apps UI.
                        </div>
                      </div>
                    );
                  })()}

                  {/* Cache + Thread pool */}
                  <div className="grid grid-cols-3 gap-3">
                    <MetricCard
                      label="Cache Memory"
                      value={`${status.cache.total_size_mb}MB`}
                      sub={`${status.cache.utilization_percent}% of ${status.cache.max_memory_mb}MB · ${status.cache.entries} entries · TTL ${Math.round(status.cache.ttl_seconds / 3600)}h`}
                      icon={Database}
                      color={status.cache.utilization_percent > 80 ? "text-amber-400" : "text-emerald-400"}
                    />
                    <MetricCard
                      label="User Sessions"
                      value={status.user_cache.entries}
                      sub={`of ${status.user_cache.max_entries} max`}
                      icon={Users}
                      color="text-emerald-400"
                    />
                    <MetricCard
                      label="Thread Pool"
                      value={status.thread_pool.max_workers}
                      sub={`${status.thread_pool.inflight_cache_keys.length} inflight queries`}
                      icon={Cpu}
                      color={status.thread_pool.inflight_cache_keys.length > 5 ? "text-amber-400" : "text-emerald-400"}
                    />
                  </div>

                  {/* Cache inventory table */}
                  <div>
                    <div className="flex items-center gap-2 mb-3">
                      <Layers size={12} className="text-emerald-500/60" />
                      <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-emerald-500/60">Cache Inventory</span>
                      <span className="font-mono text-[10px] text-emerald-500/30">{status.cache.inventory_note}</span>
                    </div>
                    <div className="bg-black/30 border border-emerald-500/10 rounded-lg overflow-hidden">
                      <div className="grid grid-cols-[1fr_100px_80px_70px_80px_50px_50px] gap-2 px-4 py-2 text-[9px] font-mono uppercase tracking-wider text-emerald-500/40 border-b border-emerald-500/10">
                        <span>Cache Key</span>
                        <span>Cached At</span>
                        <span>Size</span>
                        <span>TTL Left</span>
                        <span>Last Used</span>
                        <span>Status</span>
                        <span></span>
                      </div>
                      <div className="max-h-[240px] overflow-y-auto">
                        {status.cache.inventory.length === 0 ? (
                          <div className="px-4 py-6 text-center text-[11px] font-mono text-emerald-500/30">No cache entries</div>
                        ) : (
                          status.cache.inventory.map((entry) => (
                            <div key={entry.key} className="grid grid-cols-[1fr_100px_80px_70px_80px_50px_50px] gap-2 px-4 py-1.5 text-[11px] font-mono border-b border-emerald-500/[0.04] hover:bg-emerald-500/[0.03] items-center">
                              <span className="text-emerald-300/80 truncate" title={entry.key}>{entry.key}</span>
                              <span className="text-emerald-500/50">{new Date(entry.cached_at).toLocaleTimeString()}</span>
                              <span className={entry.size_kb > 500 ? "text-amber-400/70" : "text-emerald-500/50"}>
                                {entry.size_kb >= 1024 ? `${(entry.size_kb / 1024).toFixed(1)}MB` : `${entry.size_kb}KB`}
                              </span>
                              <span className={entry.ttl_remaining_sec < 600 ? "text-amber-400/70" : "text-emerald-500/50"}>
                                {Math.floor(entry.ttl_remaining_sec / 3600)}h {Math.floor((entry.ttl_remaining_sec % 3600) / 60)}m
                              </span>
                              <span className="text-emerald-500/40 text-[10px]">{entry.last_accessed_ago}</span>
                              <span className={`text-[9px] px-1.5 py-0.5 rounded ${entry.expired ? "bg-red-500/10 text-red-400/70" : "bg-emerald-500/10 text-emerald-400/70"}`}>
                                {entry.expired ? "EXPIRED" : "LIVE"}
                              </span>
                              <button
                                onClick={() => {
                                  fetch(`/api/admin/evict-cache?key=${encodeURIComponent(entry.key)}`, { method: "POST" })
                                    .then(() => fetchStatus());
                                }}
                                className="text-[9px] text-red-400/50 hover:text-red-400 transition-colors"
                                title={`Evict ${entry.key}`}
                              >
                                EVICT
                              </button>
                            </div>
                          ))
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Inflight queries */}
                  {status.thread_pool.inflight_cache_keys.length > 0 && (
                    <div>
                      <span className="font-mono text-[10px] uppercase tracking-[0.15em] text-amber-500/60">Inflight Queries</span>
                      <div className="mt-2 flex flex-wrap gap-2">
                        {status.thread_pool.inflight_cache_keys.map((key) => (
                          <span key={key} className="font-mono text-[10px] text-amber-300/80 bg-amber-500/10 border border-amber-500/20 px-2 py-0.5 rounded">
                            {key}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Matrix rain effect bottom */}
              <div className="px-6 py-3 border-t border-emerald-500/10 flex items-center justify-between">
                <span className="font-mono text-[9px] text-emerald-500/30">Auto-refresh: 10s</span>
                <span className="font-mono text-[9px] text-emerald-500/30">Lineage Explorer v{status?.system?.python_version || "?"}</span>
              </div>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}

export default memo(AdminDashboard);
