/** @deprecated R2 is being phased out — only used by /econ page */
const R2_PUBLIC_URL = process.env.NEXT_PUBLIC_R2_URL;
export const ECON_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/econ.json` : "";

export const TIMELINE_URL = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787/timeline";

export const GOAL = 2_000_000;
