import { memo } from "react";
import type { ReactNode } from "react";
import FeatureToggleCard from "./FeatureToggleCard";
import type { FeatureFlagCard as FlagCardType } from "../../api/controlPanel";

interface Props {
  moduleLabel: string;
  flags: FlagCardType[];
  isAdmin: boolean;
  busyFlagId: string | null;
  onToggle: (flagId: string, next: boolean) => void;
  onOpenAccessCheck: (flag: FlagCardType) => void;
  statusSlot?: ReactNode;
}

function ModuleSection({ moduleLabel, flags, isAdmin, busyFlagId, onToggle, onOpenAccessCheck, statusSlot }: Props) {
  if (flags.length === 0) return null;
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-[11px] uppercase tracking-[0.12em] text-slate-500 font-semibold">{moduleLabel}</h3>
        {statusSlot}
      </div>
      <div className="space-y-2">
        {flags.map((f) => (
          <FeatureToggleCard
            key={f.id}
            flag={f}
            isAdmin={isAdmin}
            busy={busyFlagId === f.id}
            onToggle={onToggle}
            onOpenAccessCheck={onOpenAccessCheck}
          />
        ))}
      </div>
    </div>
  );
}

export default memo(ModuleSection);
