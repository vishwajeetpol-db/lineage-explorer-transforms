import { memo, useState } from "react";
import * as Switch from "@radix-ui/react-switch";
import { ChevronRight, Lock } from "lucide-react";
import ImpactBadges from "./ImpactBadges";
import type { FeatureFlagCard as FlagCardType } from "../../api/controlPanel";

const ACCENT_STYLES: Record<string, { ring: string; dot: string; text: string; switchOn: string }> = {
  amber: { ring: "border-amber-500/15 hover:border-amber-500/35", dot: "bg-amber-400", text: "text-amber-400", switchOn: "data-[state=checked]:bg-amber-500" },
  violet: { ring: "border-violet-500/15 hover:border-violet-500/35", dot: "bg-violet-400", text: "text-violet-400", switchOn: "data-[state=checked]:bg-violet-500" },
  cyan: { ring: "border-cyan-500/15 hover:border-cyan-500/35", dot: "bg-cyan-400", text: "text-cyan-400", switchOn: "data-[state=checked]:bg-cyan-500" },
  indigo: { ring: "border-indigo-500/15 hover:border-indigo-500/35", dot: "bg-indigo-400", text: "text-indigo-400", switchOn: "data-[state=checked]:bg-indigo-500" },
};

interface Props {
  flag: FlagCardType;
  isAdmin: boolean;
  busy: boolean;
  onToggle: (flagId: string, next: boolean) => void;
  onOpenAccessCheck: (flag: FlagCardType) => void;
}

function FeatureToggleCard({ flag, isAdmin, busy, onToggle, onOpenAccessCheck }: Props) {
  const [expanded, setExpanded] = useState(false);
  const accent = ACCENT_STYLES[flag.accent] || ACCENT_STYLES.indigo;
  const toggleDisabled = !isAdmin || busy || flag.kill_switched;

  return (
    <div className={`rounded-xl bg-white/[0.02] border ${accent.ring} transition-colors duration-200 overflow-hidden`}>
      <div className="flex items-start gap-3 p-4">
        <button onClick={() => setExpanded((v) => !v)} className="flex-1 flex items-start gap-3 text-left min-w-0">
          <div className={`w-2 h-2 rounded-full ${accent.dot} mt-1.5 flex-shrink-0`} />
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold text-white">{flag.name}</span>
              {flag.kill_switched && (
                <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[9px] font-medium bg-red-500/10 text-red-400 border border-red-500/20">
                  <Lock size={9} /> Kill-switched
                </span>
              )}
              <ChevronRight size={13} className={`text-slate-500 transition-transform duration-150 ${expanded ? "rotate-90" : ""}`} />
            </div>
            <div className="text-[11px] text-slate-500 mt-0.5">{flag.module_label}</div>
            {expanded && (
              <div className="mt-3 space-y-3">
                <p className="text-[12px] text-slate-400 leading-relaxed">{flag.description}</p>
                {flag.side_effects.length > 0 && (
                  <div>
                    <div className="text-[10px] uppercase tracking-wide text-slate-500 font-medium mb-1.5">Side effects</div>
                    <ul className="space-y-1">
                      {flag.side_effects.map((s, i) => (
                        <li key={i} className="text-[11px] text-slate-400 flex gap-1.5">
                          <span className="text-slate-600">•</span> {s}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                {flag.depends_on.length > 0 && (
                  <div className="text-[11px] text-slate-500">
                    Depends on: <span className="font-mono text-slate-400">{flag.depends_on.join(", ")}</span>
                  </div>
                )}
                <button
                  onClick={(e) => { e.stopPropagation(); onOpenAccessCheck(flag); }}
                  className={`text-[11px] font-medium ${accent.text} hover:underline`}
                >
                  View access requirements ({flag.access_requirements.length})
                </button>
              </div>
            )}
          </div>
        </button>
        <div className="flex flex-col items-end gap-2 flex-shrink-0">
          <Switch.Root
            checked={flag.enabled}
            disabled={toggleDisabled}
            onCheckedChange={(next) => onToggle(flag.id, next)}
            className={`w-9 h-5 rounded-full relative bg-white/10 transition-colors ${accent.switchOn} disabled:opacity-40 disabled:cursor-not-allowed`}
            title={!isAdmin ? "Admin access required to toggle" : flag.kill_switched ? "Kill-switched by an ops env var — cannot be toggled here" : undefined}
          >
            <Switch.Thumb className="block w-4 h-4 bg-white rounded-full shadow transition-transform duration-150 translate-x-0.5 data-[state=checked]:translate-x-[18px]" />
          </Switch.Root>
          <ImpactBadges cost={flag.cost} risk={flag.risk} />
        </div>
      </div>
    </div>
  );
}

export default memo(FeatureToggleCard);
