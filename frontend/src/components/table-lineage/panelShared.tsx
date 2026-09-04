import { Loader2, AlertCircle, Database, RefreshCw, Clock, AlertTriangle } from "lucide-react";
import type { CacheMeta } from "../../api/client";

/** Shared building blocks for the Table Lineage capability panels — keeps every
 *  panel visually consistent with the app's dark/indigo surface theme. */

export function PanelState({
  loading,
  error,
  empty,
  emptyLabel = "No data for this table.",
  children,
}: {
  loading: boolean;
  error: string | null;
  empty: boolean;
  emptyLabel?: string;
  children: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-500">
        <Loader2 size={22} className="animate-spin text-accent" />
        <span className="text-[12px]">Loading…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-center px-6">
        <AlertCircle size={22} className="text-rose-400" />
        <span className="text-[12px] text-rose-300 max-w-md break-words">{error}</span>
      </div>
    );
  }
  if (empty) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-600">
        <Database size={22} />
        <span className="text-[12px]">{emptyLabel}</span>
      </div>
    );
  }
  return <>{children}</>;
}

export function NoTable() {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-slate-600 text-center px-6">
      <Database size={22} />
      <span className="text-[12px]">Select a table from the tree to see its details.</span>
    </div>
  );
}

/** A small labeled stat tile. */
export function Stat({ label, value, accent = "text-slate-100" }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div className="rounded-xl bg-surface-100/60 border border-white/[0.06] px-4 py-3">
      <div className="text-[10px] uppercase tracking-wider text-slate-500 font-medium">{label}</div>
      <div className={`text-[18px] font-semibold mt-0.5 ${accent}`}>{value}</div>
    </div>
  );
}

/** Section heading. */
export function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[10px] uppercase tracking-wider text-slate-500 font-medium mb-2 mt-4 first:mt-0">
      {children}
    </div>
  );
}

const SEV_STYLES: Record<string, string> = {
  PII: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  PCI: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  EMAIL: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  SENSITIVE: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30",
};

export function SensitivityBadge({ sensitivity }: { sensitivity: string }) {
  const cls = SEV_STYLES[sensitivity.toUpperCase()] || "bg-violet-500/15 text-violet-300 border-violet-500/30";
  return (
    <span className={`text-[9px] px-1.5 py-0.5 rounded border font-medium uppercase tracking-wide ${cls}`}>
      {sensitivity}
    </span>
  );
}

/** Human "x ago" from an ISO timestamp. */
function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

/** Cache status + refresh control shown at the top of each capability panel.
 *  Displays "cached Xh ago" (amber "stale" once past TTL), and a refresh icon
 *  that re-fetches live data. Renders nothing until `cache` meta is available. */
export function CacheHeader({
  cache,
  loading,
  onRefresh,
}: {
  cache: CacheMeta | null | undefined;
  loading: boolean;
  onRefresh: () => void;
}) {
  const stale = !!cache?.stale;
  return (
    <div className="flex items-center gap-2 mb-3">
      {cache ? (
        <span
          className={`flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded border ${
            stale
              ? "bg-amber-500/10 text-amber-300 border-amber-500/30"
              : "bg-surface-100/60 text-slate-400 border-white/[0.08]"
          }`}
          title={cache.cached_at ? `Cached at ${cache.cached_at}${cache.cached_by ? ` by ${cache.cached_by}` : ""}` : undefined}
        >
          {stale ? <AlertTriangle size={10} /> : <Clock size={10} />}
          {cache.from_cache ? `cached ${timeAgo(cache.cached_at)}` : "just refreshed"}
          {stale && " · may be stale"}
        </span>
      ) : (
        <span className="text-[10px] text-slate-600">live</span>
      )}
      <button
        onClick={onRefresh}
        disabled={loading}
        title="Refresh — fetch current data and update the cache"
        className="ml-auto flex items-center gap-1 text-[10px] px-2 py-1 rounded-lg bg-surface-100/60 hover:bg-white/[0.06] border border-white/[0.08] text-slate-300 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
      >
        <RefreshCw size={11} className={loading ? "animate-spin" : ""} />
        Refresh
      </button>
    </div>
  );
}

/** Parse a "catalog.schema.table" FQN into parts (or null if not 3-part). */
export function parseFqn(fqn: string | null): { catalog: string; schema: string; table: string } | null {
  if (!fqn) return null;
  const parts = fqn.split(".");
  if (parts.length !== 3) return null;
  return { catalog: parts[0], schema: parts[1], table: parts[2] };
}
