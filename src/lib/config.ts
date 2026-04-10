const R2_PUBLIC_URL = process.env.NEXT_PUBLIC_R2_URL;
/** @deprecated R2 is being phased out */
export const REPORT_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/latest.json` : "";
export const ECON_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/econ.json` : "";

export const TIMELINE_URL = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787/timeline";

export const GOAL = 2_000_000;
