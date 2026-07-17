import { memo } from "react";
import { Gauge, ShieldAlert } from "lucide-react";

const LEVEL_STYLES: Record<string, string> = {
  low: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  medium: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  high: "text-red-400 bg-red-500/10 border-red-500/20",
};

interface Props {
  cost: string;
  risk: string;
}

function ImpactBadges({ cost, risk }: Props) {
  return (
    <div className="flex items-center gap-1.5">
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${LEVEL_STYLES[cost] || LEVEL_STYLES.low}`}
        title="Estimated cost impact of enabling this capability"
      >
        <Gauge size={10} /> Cost: {cost}
      </span>
      <span
        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border ${LEVEL_STYLES[risk] || LEVEL_STYLES.low}`}
        title="Estimated risk impact of enabling this capability"
      >
        <ShieldAlert size={10} /> Risk: {risk}
      </span>
    </div>
  );
}

export default memo(ImpactBadges);
