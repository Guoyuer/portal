// NEXT_PUBLIC_TIMELINE_URL is a *base* URL — endpoint suffixes live here, not in
// the env var. Same convention as NEXT_PUBLIC_GMAIL_WORKER_URL in use-mail.ts.
export const WORKER_BASE = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787";

export const TIMELINE_URL = `${WORKER_BASE}/timeline`;
export const ECON_URL = `${WORKER_BASE}/econ`;

export const GOAL = 2_000_000;
