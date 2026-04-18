// ── Edge-cache TTLs (seconds) ────────────────────────────────────────────
// /timeline refreshes on nightly sync + local sync; 60s staleness is
// invisible to a human reloading the dashboard. /econ and /prices rarely
// change intraday so we cache harder.

export const TTLS = {
  timeline: 60,
  econ: 600,
  prices: 300,
} as const;
