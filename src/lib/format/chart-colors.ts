// ── Chart color palette (Okabe-Ito, protanomaly-safe) ──────────────────
//
// Single source of truth for chart fills / strokes. Category colors used by
// the allocation donut + stacked area come from this palette keyed on the
// camelCase category key. Trade markers (buy green / sell orange) draw from
// the same palette.
//
// Pipeline owns category name/order/target; colors live frontend-only so the
// palette can be tuned without re-syncing D1.

const OKABE_ITO = {
  blue: "#0072B2",
  green: "#009E73",
  orange: "#E69F00",
  skyBlue: "#56B4E9",
} as const;

// ── Trade markers ───────────────────────────────────────────────────────

export const BUY_COLOR = OKABE_ITO.green;
export const SELL_COLOR = OKABE_ITO.orange;

// ── Return badge palette ───────────────────────────────────────────────
//
// Softer than Okabe-Ito buy/sell so small inline ±% badges (market
// indices, holdings gain/loss) don't compete visually with chart markers.

export const MARKET_GAIN = "#81b29a";
export const MARKET_LOSS = "#cd6155";

// ── Category colors (keyed on bundle's camelCase category key) ─────────

export const CAT_COLOR_BY_KEY: Record<string, string> = {
  usEquity: OKABE_ITO.blue,
  nonUsEquity: OKABE_ITO.green,
  crypto: OKABE_ITO.orange,
  safeNet: OKABE_ITO.skyBlue,
};

// ── Economy page line colors ───────────────────────────────────────────
//
// Distinct palette from the Okabe-Ito allocation colors above — econ charts
// never mix red and green on the same axis (inflation uses red + amber;
// rates use blue + violet + amber; single-line charts use one of red / blue
// / green) so the protanomaly risk is already mitigated by layout, not
// palette constraints.

export const ECON_LINE_COLORS = {
  blue: "#2563eb",
  violet: "#7c3aed",
  amber: "#f59e0b",
  red: "#ef4444",
  green: "#10b981",
} as const;
