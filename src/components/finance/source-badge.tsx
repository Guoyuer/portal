import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";
import type { SourceKind } from "@/lib/compute/computed-types";

// Short source labels pair the color-coded background with readable text
// so the badge is distinguishable without color alone (protanomaly-safe).
export const SOURCE_SHORT_LABEL: Record<SourceKind, string> = {
  fidelity: "FID",
  robinhood: "RH",
  "401k": "401k",
};

export const SOURCE_FULL_LABEL: Record<SourceKind, string> = {
  fidelity: "Fidelity",
  robinhood: "Robinhood",
  "401k": "401k",
};

const COLORS: Record<SourceKind, string> = {
  fidelity: CAT_COLOR_BY_KEY.usEquity,     // Okabe-Ito blue
  robinhood: CAT_COLOR_BY_KEY.nonUsEquity, // Okabe-Ito green
  "401k": CAT_COLOR_BY_KEY.crypto,         // Okabe-Ito orange
};

export type { SourceKind };

export function SourceBadge({ source }: { source: SourceKind }) {
  const color = COLORS[source];
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ backgroundColor: color + "33", color }}
      aria-label={`source: ${source}`}
    >
      {SOURCE_SHORT_LABEL[source]}
    </span>
  );
}
