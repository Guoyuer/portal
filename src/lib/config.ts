const R2_PUBLIC_URL = process.env.NEXT_PUBLIC_R2_URL;
/** @deprecated Use API_BASE + endpoint instead */
export const REPORT_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/latest.json` : "";
export const ECON_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/econ.json` : "";

export const TIMELINE_URL = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8000/timeline";
export const API_BASE = TIMELINE_URL.replace(/\/timeline$/, "");

export const GOAL = 1_000_000;
