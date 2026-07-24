import { Loader2, AlertCircle, Database } from "lucide-react";

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

/** Parse a "catalog.schema.table" FQN into parts (or null if not 3-part). */
export function parseFqn(fqn: string | null): { catalog: string; schema: string; table: string } | null {
  if (!fqn) return null;
  const parts = fqn.split(".");
  if (parts.length !== 3) return null;
  return { catalog: parts[0], schema: parts[1], table: parts[2] };
}
