// NEXT_PUBLIC_TIMELINE_URL is a *base* URL — endpoint suffixes live here, not in
// the env var. `||` — not `??` — so an accidentally-empty env var (a GH Actions
// secret set to "" still arrives as "", not undefined) still falls back rather
// than baking a broken origin into the bundle.
export const WORKER_BASE = process.env.NEXT_PUBLIC_TIMELINE_URL || "http://localhost:8787";

export const TIMELINE_URL = `${WORKER_BASE}/timeline`;
export const ECON_URL = `${WORKER_BASE}/econ`;
export const PRICES_URL = `${WORKER_BASE}/prices`;

export const GOAL = 2_000_000;

/** Abort bundle/econ fetches after this long. */
export const FETCH_TIMEOUT_MS = 10_000;
