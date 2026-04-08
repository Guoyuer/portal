const R2_PUBLIC_URL = process.env.NEXT_PUBLIC_R2_URL;
if (!R2_PUBLIC_URL) {
  console.warn("NEXT_PUBLIC_R2_URL not set — report data will not load");
}
export const REPORT_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/latest.json` : "";
export const ECON_URL = R2_PUBLIC_URL ? `${R2_PUBLIC_URL}/reports/econ.json` : "";

export const TIMELINE_URL = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8000/timeline";
export const ALLOCATION_URL = process.env.NEXT_PUBLIC_ALLOCATION_URL ?? "http://localhost:8000/allocation";
