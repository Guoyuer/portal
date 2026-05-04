import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";
import type { SourceKind } from "@/lib/compute/computed-types";

// Short labels pair the color-coded background with readable text so badges
// are distinguishable without color alone (protanomaly-safe).
export const SOURCE_META: Record<SourceKind, { short: string; full: string; color: string }> = {
  fidelity: { short: "FID", full: "Fidelity", color: CAT_COLOR_BY_KEY.usEquity },
  robinhood: { short: "RH", full: "Robinhood", color: CAT_COLOR_BY_KEY.nonUsEquity },
  "401k": { short: "401k", full: "401k", color: CAT_COLOR_BY_KEY.crypto },
};

export function SourceBadge({ source }: { source: SourceKind }) {
  const { color, short } = SOURCE_META[source];
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ backgroundColor: color + "33", color }}
      aria-label={`source: ${source}`}
    >
      {short}
    </span>
  );
}
