/**
 * FreshnessBadge — compact pill showing transform lineage staleness.
 *
 * Green dot + "2h ago" = fresh
 * Yellow dot + "3d ago" = stale
 * Gray dot + "Never built" = no lineage exists
 */
import type { FreshnessInfo } from '../../api/transform';

interface FreshnessBadgeProps {
  freshness: FreshnessInfo;
}

export function FreshnessBadge({ freshness }: FreshnessBadgeProps) {
  const { exists, is_stale, age_str, edge_count } = freshness;

  let dotColor = 'bg-gray-500';
  let textColor = 'text-gray-400';
  let label = age_str;

  if (!exists) {
    dotColor = 'bg-gray-500';
    textColor = 'text-gray-500';
    label = 'Not built';
  } else if (is_stale) {
    dotColor = 'bg-yellow-400';
    textColor = 'text-yellow-400';
  } else {
    dotColor = 'bg-green-400';
    textColor = 'text-green-400';
  }

  return (
    <div
      className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-gray-800/60 border border-gray-700"
      title={`${edge_count} transformation edges \u2022 ${age_str}`}
    >
      <span className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
      <span className={`text-[10px] ${textColor} font-medium`}>{label}</span>
    </div>
  );
}
