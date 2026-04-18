// ── Economic-indicator value formatters ──────────────────────────────────
// Shared between the macro snapshot cards and the time-series chart legend,
// so the same dashboard reading ("Fed Rate 4.25%") renders consistently
// whether the user sees it in a card or a tooltip.

const pct2 = (v: number) => `${v.toFixed(2)}%`;
const pct1 = (v: number) => `${v.toFixed(1)}%`;
const dec1 = (v: number) => v.toFixed(1);
const dec4 = (v: number) => v.toFixed(4);
const dollar0 = (v: number) => `$${v.toFixed(0)}`;
const bps = (v: number) => `${(v * 100).toFixed(0)} bps`;

export const ECON_FORMATTERS: Record<string, (v: number) => string> = {
  fedFundsRate: pct2,
  treasury10y: pct2,
  treasury2y: pct2,
  cpiYoy: pct1,
  coreCpiYoy: pct1,
  unemployment: pct1,
  vix: dec1,
  dxy: dec1,
  usdCny: dec4,
  oilWti: dollar0,
  spread2s10s: bps,
};

/** Snapshot-card spread: prefix "+" for positive so readers distinguish it from
 *  the flat-line case. Chart tooltips use the plain `bps` variant because the
 *  y-axis already shows sign via axis position. */
export const fmtSpreadSigned = (v: number) =>
  `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)} bps`;
