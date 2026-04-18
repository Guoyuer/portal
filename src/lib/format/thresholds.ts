// ── Business thresholds + value-based color helpers ──────────────────────

/** Green for positive, red for negative values (gain/loss, returns, etc.) */
export function valueColor(val: number): string {
  return val >= 0 ? "text-emerald-700 dark:text-cyan-400" : "text-red-600 dark:text-red-400";
}

// ── Savings rate thresholds ──────────────────────────────────────────────

export const SAVINGS_RATE_GOOD = 30;
export const SAVINGS_RATE_WARNING = 15;

// ── Component-level thresholds ───────────────────────────────────────────

/** Cash-flow: expenses below this are grouped into "... and N more" */
export const MAJOR_EXPENSE_THRESHOLD = 200;

/** Cash-flow: income items below this are folded into "Other" */
export const SMALL_INCOME_THRESHOLD = 10;


/** Back-to-top button: show after scrolling past this many pixels */
export const SCROLL_SHOW_THRESHOLD = 600;
