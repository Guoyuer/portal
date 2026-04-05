// ── Shared style helpers & constants ──────────────────────────────────────
// Centralizes color logic and magic numbers used across finance components.

// ── Value-based color classes ────────────────────────────────────────────

/** Green for positive, red for negative values (gain/loss, returns, etc.) */
export function valueColor(val: number): string {
  return val >= 0 ? "text-green-600" : "text-red-500";
}

// ── Savings rate thresholds ──────────────────────────────────────────────

const SAVINGS_RATE_GOOD = 30;
const SAVINGS_RATE_WARNING = 15;

/** Green / yellow / red based on savings rate thresholds. */
export function savingsRateColor(rate: number): string {
  if (rate >= SAVINGS_RATE_GOOD) return "text-green-600";
  if (rate >= SAVINGS_RATE_WARNING) return "text-yellow-600";
  return "text-red-500";
}

// ── Component-level thresholds ───────────────────────────────────────────

/** Cash-flow: expenses below this are grouped into "... and N more" */
export const MAJOR_EXPENSE_THRESHOLD = 200;

/** Balance sheet: group credit cards into one row when total < this */
export const CC_GROUP_THRESHOLD = 500;

/** Back-to-top button: show after scrolling past this many pixels */
export const SCROLL_SHOW_THRESHOLD = 600;
