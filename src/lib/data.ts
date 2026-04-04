import type { ReportData } from "./types";

// report-data.json is downloaded from R2 before build in CI.
// eslint-disable-next-line @typescript-eslint/no-require-imports
export const reportData: ReportData = require("./report-data.json") as ReportData;
