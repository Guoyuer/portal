const WORKER_BASE = (process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787/timeline").replace(/\/timeline$/, "");

export const TIMELINE_URL = `${WORKER_BASE}/timeline`;
export const ECON_URL = `${WORKER_BASE}/econ`;

export const GOAL = 2_000_000;
