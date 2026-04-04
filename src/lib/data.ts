import type { ReportData } from "./types";
import sampleReport from "./sample-report.json";

// In CI, report-data.json is downloaded from R2 before build.
// Locally or when R2 is empty, it falls back to the bundled sample.
let reportData: ReportData;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  reportData = require("./report-data.json") as ReportData;
} catch {
  reportData = sampleReport as unknown as ReportData;
}

export { reportData };
