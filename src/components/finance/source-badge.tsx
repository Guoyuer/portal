import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";

// Short source labels pair the color-coded background with readable text
// so the badge is distinguishable without color alone (protanomaly-safe).
const LABELS = {
  fidelity: "FID",
  robinhood: "RH",
  "401k": "401k",
} as const;

const COLORS = {
  fidelity: CAT_COLOR_BY_KEY.usEquity,     // Okabe-Ito blue
  robinhood: CAT_COLOR_BY_KEY.nonUsEquity, // Okabe-Ito green
  "401k": CAT_COLOR_BY_KEY.crypto,         // Okabe-Ito orange
} as const;

export type SourceKind = keyof typeof LABELS;

export function SourceBadge({ source }: { source: SourceKind }) {
  const color = COLORS[source];
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ backgroundColor: color + "33", color }}
      aria-label={`source: ${source}`}
    >
      {LABELS[source]}
    </span>
  );
}
