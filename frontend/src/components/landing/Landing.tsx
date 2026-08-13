import { memo, useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import {
  Search, FolderOpen, ChevronRight, Loader2, RefreshCw, Layers, FolderTree, GitBranch,
  Home, GitBranchPlus, Network, ShieldCheck, ScrollText, FileBarChart, Settings as SettingsIcon,
  Bell, HelpCircle, Sun, Moon, Database, ChevronDown, ChevronRight as ChevronR, ChevronLeft,
  Activity, TableProperties, Workflow, AlertTriangle, FilePenLine, ArrowRight, Shield,
} from "lucide-react";
import { useLineageStore } from "../../store/lineageStore";
import { api } from "../../api/client";
import { useThemeStore } from "../../store/themeStore";
import { goCatalogs, goTableLineage, goRootCause, goDQ, goControlPanel, goAdmin } from "../../hooks/useRouter";
import LineagePicker from "./LineagePicker";

interface Props {
  onSelectTable: (fqdn: string) => void;
}

// ---- Sidebar nav ----------------------------------------------------------
type NavItem = { label: string; icon: typeof Home; action?: () => void; active?: boolean; adminOnly?: boolean };
const NAV: NavItem[] = [
  { label: "Home", icon: Home, active: true },
  { label: "Search", icon: Search, action: () => useLineageStore.getState().setGlobalSearchOpen(true) },
  { label: "Browse", icon: FolderOpen, action: goCatalogs },
  { label: "Lineage Explorer", icon: Network, action: goCatalogs },
  { label: "Impact Analysis", icon: GitBranchPlus, action: () => goTableLineage() },
  { label: "Data Quality", icon: ShieldCheck, action: () => goDQ() },
  { label: "Reports", icon: FileBarChart, action: goRootCause },
  { label: "Settings", icon: SettingsIcon, action: goControlPanel },
  { label: "Admin Dashboard", icon: Shield, action: goAdmin, adminOnly: true },
];

// ---- Recent activity (from notifications) ---------------------------------
interface Notif {
  id?: string | number;
  type?: string;
  severity?: string;
  title?: string;
  detail?: string;
  table_fqn?: string;
  created_at?: string;
}
const ACTIVITY_ICON: Record<string, { icon: typeof Activity; color: string }> = {
  new_table: { icon: TableProperties, color: "text-emerald-400" },
  schema_change: { icon: FilePenLine, color: "text-violet-400" },
  pipeline: { icon: Workflow, color: "text-sky-400" },
  dq_alert: { icon: AlertTriangle, color: "text-amber-400" },
};
function activityMeta(n: Notif) {
  const t = (n.type || "").toLowerCase();
  if (t.includes("schema")) return ACTIVITY_ICON.schema_change;
  if (t.includes("pipeline") || t.includes("run")) return ACTIVITY_ICON.pipeline;
  if (t.includes("dq") || t.includes("quality") || n.severity === "high") return ACTIVITY_ICON.dq_alert;
  return ACTIVITY_ICON.new_table;
}
function timeAgo(iso?: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function Tile({
  icon: Icon, iconColor, gradient, edge, title, subtitle, cta, ctaColor, delay, onClick,
}: {
  icon: typeof FolderOpen; iconColor: string; gradient: string; edge: string; title: string;
  subtitle: string; cta: string; ctaColor: string; delay: number; onClick: () => void;
}) {
  return (
    <motion.button
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay }}
      whileHover={{ y: -3 }}
      onClick={onClick}
      className="group relative overflow-hidden flex flex-col items-center text-center gap-4 px-6 py-8 bg-surface-50/50 hover:bg-surface-50/80 border border-white/[0.06] hover:border-white/[0.12] rounded-2xl transition-all duration-200"
    >
      {/* Colored top edge — gives each tile its own identity (mirrors the panels). */}
      <span className={`absolute top-0 inset-x-0 h-1 bg-gradient-to-r to-transparent ${edge}`} />
      <div className={`w-16 h-16 rounded-full flex items-center justify-center bg-gradient-to-br ${gradient}`}>
        <Icon size={26} className={iconColor} />
      </div>
      <div>
        <div className="text-[16px] font-semibold text-slate-100">{title}</div>
        <div className="text-[12px] text-slate-500 mt-1 max-w-[190px]">{subtitle}</div>
      </div>
      <div className={`flex items-center gap-1.5 text-[12px] font-medium ${ctaColor}`}>
        {cta} <ArrowRight size={13} className="group-hover:translate-x-0.5 transition-transform" />
      </div>
    </motion.button>
  );
}

function Landing({ onSelectTable }: Props) {
  const allTables = useLineageStore((s) => s.allTables);
  const allTablesLoading = useLineageStore((s) => s.allTablesLoading);
  const setGlobalSearchOpen = useLineageStore((s) => s.setGlobalSearchOpen);
  const isAdmin = useLineageStore((s) => s.isAdmin);
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const [pickerMode, setPickerMode] = useState<"schema" | "catalog" | null>(null);
  const [navCollapsed, setNavCollapsed] = useState(false);
  const [activity, setActivity] = useState<Notif[]>([]);
  const [unread, setUnread] = useState(0);
  const [userEmail, setUserEmail] = useState<string | null>(null);

  const catalogCount = useMemo(() => new Set(allTables.map((t) => t.catalog)).size, [allTables]);

  useEffect(() => {
    api.getUserInfo().then((u) => setUserEmail(u.email)).catch(() => {});
    fetch("/api/notifications?limit=4").then((r) => r.ok ? r.json() : null).then((d) => {
      if (d?.notifications) setActivity(d.notifications);
    }).catch(() => {});
    fetch("/api/notifications/unread-count").then((r) => r.ok ? r.json() : null).then((d) => {
      if (typeof d?.count === "number") setUnread(d.count);
    }).catch(() => {});
  }, []);

  const initials = (userEmail || "AD").slice(0, 2).toUpperCase();

  // Loading / empty states keep the shell so it never flashes bare.
  const centerLoading = allTablesLoading;
  const centerEmpty = !allTablesLoading && allTables.length === 0;

  return (
    <div className="h-screen w-screen flex bg-surface overflow-hidden">
      {/* ---- Sidebar (maroon collapsible rail) ---- */}
      {/* Fixed hex text colors (not the theme-tokenized text-white / text-rose-*
          utilities) so the rail stays legible on maroon in BOTH light + dark. */}
      <aside className={`shrink-0 flex flex-col border-r border-white/10 bg-gradient-to-b from-[#4a0d17] to-[#29070f] text-[#fff1f2] transition-[width] duration-300 ease-out ${navCollapsed ? "w-[68px]" : "w-[248px]"}`}>
        {/* Logo */}
        <div className={`flex items-center gap-2.5 h-[68px] shrink-0 ${navCollapsed ? "justify-center px-0" : "px-5"}`}>
          <img src="/bricktrace-logo.png" alt="" className="w-10 h-10 object-contain shrink-0" />
          {!navCollapsed && (
            <span className="text-[19px] font-bold tracking-tight whitespace-nowrap">
              <span className="text-[#fff1f2]">Brick</span><span className="text-[#FF8A66]">Trace</span>
            </span>
          )}
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-2 space-y-1 overflow-y-auto overflow-x-hidden">
          {NAV.filter((item) => !item.adminOnly || isAdmin).map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.label}
                onClick={item.action}
                title={navCollapsed ? item.label : undefined}
                className={`w-full flex items-center gap-3 py-2.5 rounded-xl text-[13px] font-medium transition-all whitespace-nowrap ${navCollapsed ? "justify-center px-0" : "px-4"} ${
                  item.active
                    ? "bg-rose-500/30 text-[#fff1f2] border border-rose-300/50 shadow-[inset_0_0_16px_rgba(244,63,94,0.25)]"
                    : "text-[#ffe4e6]/75 hover:text-[#fff1f2] hover:bg-white/[0.07] border border-transparent"
                }`}
              >
                <Icon size={17} className="shrink-0" />
                {!navCollapsed && item.label}
              </button>
            );
          })}
        </nav>

        {/* Workspace selector */}
        <div className="px-3 pb-3">
          {navCollapsed ? (
            <div className="flex justify-center py-2 text-[#fecdd3]/75" title="All Workspaces">
              <Database size={16} />
            </div>
          ) : (
            <div className="rounded-xl border border-white/10 bg-black/25 px-3 py-2.5">
              <div className="text-[9px] uppercase tracking-wider text-[#fecdd3]/55 font-medium mb-1">Workspace</div>
              <div className="flex items-center gap-2">
                <Database size={14} className="text-[#fecdd3]/75" />
                <span className="text-[12px] text-[#fff1f2] flex-1 truncate">All Workspaces</span>
                <ChevronDown size={14} className="text-[#fecdd3]/65" />
              </div>
            </div>
          )}
        </div>

        {/* User */}
        <div className="px-3 pb-2 border-t border-white/10 pt-3">
          <button
            title={navCollapsed ? (userEmail || "User") : undefined}
            className={`w-full flex items-center gap-2.5 py-1.5 rounded-xl hover:bg-white/[0.07] transition-colors ${navCollapsed ? "justify-center px-0" : "px-2"}`}
          >
            <span className="w-9 h-9 rounded-full bg-gradient-to-br from-orange-500 to-rose-600 flex items-center justify-center text-[12px] font-bold text-[#fff1f2] shrink-0">
              {initials}
            </span>
            {!navCollapsed && (
              <>
                <div className="text-left flex-1 min-w-0">
                  <div className="text-[12px] font-semibold text-[#fff1f2] truncate">{userEmail ? userEmail.split("@")[0] : "User"}</div>
                  <div className="text-[10px] text-[#fecdd3]/65 truncate">{userEmail || "—"}</div>
                </div>
                <ChevronR size={14} className="text-[#fecdd3]/65 shrink-0" />
              </>
            )}
          </button>
        </div>

        {/* Collapse toggle — pinned to the bottom, matching the workspace rail. */}
        <button
          onClick={() => setNavCollapsed((v) => !v)}
          aria-label={navCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          className={`flex items-center gap-2.5 py-3 border-t border-white/10 bg-black/20 text-[#fecdd3]/75 hover:text-[#fff1f2] text-[12px] transition-colors ${navCollapsed ? "justify-center px-0" : "px-5"}`}
        >
          <ChevronLeft size={15} className={`shrink-0 transition-transform ${navCollapsed ? "rotate-180" : ""}`} />
          {!navCollapsed && <span>Collapse</span>}
        </button>
      </aside>

      {/* ---- Main ---- */}
      <div className="flex-1 flex flex-col min-w-0 relative overflow-y-auto">
        <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_top,rgba(99,102,241,0.05)_0%,transparent_55%)] pointer-events-none" />

        {/* Top-right bar */}
        <div className="flex items-center justify-end gap-3 px-6 h-[68px] shrink-0 relative z-10">
          <button onClick={() => useLineageStore.getState().setGlobalSearchOpen(true)}
            className="relative text-slate-400 hover:text-slate-200 transition-colors" title="Notifications">
            <Bell size={19} />
            {unread > 0 && (
              <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-4 px-1 rounded-full bg-rose-500 text-white text-[9px] font-bold flex items-center justify-center">
                {unread > 9 ? "9+" : unread}
              </span>
            )}
          </button>
          <button className="text-slate-400 hover:text-slate-200 transition-colors" title="Help"><HelpCircle size={19} /></button>
          <button onClick={toggleTheme} className="text-slate-400 hover:text-slate-200 transition-colors"
            title={theme === "dark" ? "Light mode" : "Dark mode"}>
            {theme === "dark" ? <Sun size={19} /> : <Moon size={19} />}
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 flex flex-col items-center px-8 pb-10 relative z-10">
          {/* Hero */}
          <motion.div initial={{ opacity: 0, y: -8 }} animate={{ opacity: 1, y: 0 }}
            className="flex items-center gap-4 mt-6 mb-2">
            <img src="/bricktrace-logo.png" alt="" className="w-20 h-20 object-contain drop-shadow-[0_0_28px_rgba(255,85,32,0.45)]" />
            <span className="text-[38px] font-bold tracking-tight leading-none">
              <span className="text-slate-100">Brick</span><span className="text-[#FF4520]">Trace</span>
            </span>
          </motion.div>
          <p className="text-[14px] text-slate-500 mb-10">Click any table to explore its data lineage</p>

          {centerLoading ? (
            <div className="flex flex-col items-center gap-3 py-20 text-slate-500">
              <Loader2 size={22} className="animate-spin text-accent" /> <span className="text-[13px]">Loading tables…</span>
            </div>
          ) : centerEmpty ? (
            <div className="flex flex-col items-center gap-3 py-20">
              <span className="text-[13px] text-slate-500">Unable to load table index</span>
              <button onClick={() => {
                useLineageStore.getState().setAllTablesLoading(true);
                api.getTables().then((r) => useLineageStore.getState().setAllTables(r.tables))
                  .catch(() => useLineageStore.getState().setAllTablesLoading(false));
              }} className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 hover:bg-accent/20 border border-accent/20 text-accent-light text-[12px] font-medium">
                <RefreshCw size={13} /> Retry
              </button>
            </div>
          ) : (
            <>
              {/* Tiles */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 w-full max-w-[1100px]">
                <Tile icon={FolderOpen} iconColor="text-violet-400" gradient="from-violet-500/20 to-purple-500/10" edge="from-violet-500/70"
                  title="Browse" subtitle={`${catalogCount} catalog${catalogCount !== 1 ? "s" : ""} · ${allTables.length.toLocaleString()} tables`}
                  cta="Explore" ctaColor="text-violet-400" delay={0.05} onClick={goCatalogs} />
                <Tile icon={Layers} iconColor="text-sky-400" gradient="from-sky-500/20 to-cyan-500/10" edge="from-sky-500/70"
                  title="Schema lineage" subtitle="Map every table in a schema"
                  cta="Choose schema" ctaColor="text-sky-400" delay={0.1} onClick={() => setPickerMode("schema")} />
                <Tile icon={FolderTree} iconColor="text-fuchsia-400" gradient="from-fuchsia-500/20 to-purple-500/10" edge="from-fuchsia-500/70"
                  title="Catalog lineage" subtitle="Map every table in a catalog"
                  cta="Choose catalog" ctaColor="text-fuchsia-400" delay={0.15} onClick={() => setPickerMode("catalog")} />
                <Tile icon={GitBranch} iconColor="text-orange-400" gradient="from-rose-500/20 to-orange-500/10" edge="from-orange-500/70"
                  title="Table Lineage" subtitle="Impact, governance, access & more"
                  cta="Open suite" ctaColor="text-orange-400" delay={0.2} onClick={() => goTableLineage()} />
              </div>

              {/* Search bar */}
              <motion.button initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.24 }}
                onClick={() => setGlobalSearchOpen(true)}
                className="group w-full max-w-[1100px] mt-6 flex items-center gap-3 px-5 py-4 bg-surface-50/50 hover:bg-surface-50/80 border border-white/[0.06] hover:border-accent/40 rounded-2xl transition-all">
                <Search size={17} className="text-slate-500 group-hover:text-accent-light transition-colors" />
                <span className="text-[14px] text-slate-500 group-hover:text-slate-300 transition-colors flex-1 text-left">
                  Search any table across all catalogs and schemas
                </span>
                <kbd className="text-[10px] text-slate-500 bg-surface-200 px-1.5 py-0.5 rounded font-mono">⌘K</kbd>
              </motion.button>

              {/* Recent Activity */}
              {activity.length > 0 && (
                <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.3 }}
                  className="w-full max-w-[1100px] mt-6 rounded-2xl border border-white/[0.06] bg-surface-50/40 p-5">
                  <div className="flex items-center gap-2 mb-4">
                    <Activity size={16} className="text-accent" />
                    <span className="text-[14px] font-semibold text-slate-100">Recent Activity</span>
                  </div>
                  <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                    {activity.slice(0, 4).map((n, i) => {
                      const m = activityMeta(n);
                      const Icon = m.icon;
                      return (
                        <button key={n.id ?? i} onClick={() => n.table_fqn && onSelectTable(n.table_fqn)}
                          className="flex items-start gap-2.5 text-left group">
                          <span className={`w-9 h-9 rounded-full bg-surface-100/60 border border-white/[0.06] flex items-center justify-center shrink-0`}>
                            <Icon size={15} className={m.color} />
                          </span>
                          <div className="min-w-0">
                            <div className="text-[12px] font-semibold text-slate-200 truncate group-hover:text-accent-light transition-colors">{n.title || n.type || "Activity"}</div>
                            {(n.table_fqn || n.detail) && <div className="text-[11px] text-slate-500 truncate">{n.table_fqn || n.detail}</div>}
                            <div className="text-[10px] text-slate-600 mt-0.5">{timeAgo(n.created_at)}</div>
                          </div>
                        </button>
                      );
                    })}
                  </div>
                </motion.div>
              )}
            </>
          )}
        </div>
      </div>

      {pickerMode && <LineagePicker mode={pickerMode} onClose={() => setPickerMode(null)} />}
    </div>
  );
}

export default memo(Landing);
