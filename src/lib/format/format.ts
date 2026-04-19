export function fmtQty(q: number): string {
  if (Number.isInteger(q)) return q.toString();
  return q.toFixed(4).replace(/\.?0+$/, "");
}

export function fmtCurrency(val: number): string {
  const abs = Math.abs(val);
  const decimals = abs >= 1000 ? 0 : 2;
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return val < 0 ? `-$${formatted}` : `$${formatted}`;
}

export function fmtCurrencyShort(val: number): string {
  if (val === 0) return "$0";
  const sign = val < 0 ? "-" : "";
  const abs = Math.abs(val);
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${sign}$${(abs / 1_000).toFixed(0)}k`;
  return fmtCurrency(val);
}

export function fmtPct(val: number, signed: boolean): string {
  if (signed) {
    const sign = val >= 0 ? "+" : "";
    return `${sign}${val.toFixed(1)}%`;
  }
  return `${val.toFixed(1)}%`;
}

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function fmtMonth(m: string): string {
  const monthIdx = parseInt(m.slice(5, 7), 10) - 1;
  return MONTH_NAMES[monthIdx] ?? m;
}

export function fmtMonthYear(m: string): string {
  const monthIdx = parseInt(m.slice(5, 7), 10) - 1;
  const year = m.slice(2, 4);
  return `${MONTH_NAMES[monthIdx] ?? m} ${year}`;
}

// ── Date handling (timezone-safe — avoids UTC parse of "YYYY-MM-DD") ────

/** Parse "YYYY-MM-DD" as *local* midnight, not UTC.
 *
 * ``new Date("2026-04-14")`` parses as UTC, so in any timezone west of UTC
 * its ``toLocaleDateString`` renders as the previous calendar day (e.g. NY
 * sees Apr 13). Chart X-axis timestamps need this helper — otherwise every
 * daily tick shifts by one day for US-based viewers.
 */
export function parseLocalDate(iso: string): Date {
  const [y, m, d] = iso.split("-");
  return new Date(+y, +m - 1, +d);
}

/** Coerce a ``YYYY-MM-DD`` string (parsed as local midnight), full ISO
 * timestamp, epoch-ms number, or ``Date`` into a ``Date``. */
function toDate(input: string | number | Date): Date {
  if (input instanceof Date) return input;
  if (typeof input === "number") return new Date(input);
  return input.length === 10 ? parseLocalDate(input) : new Date(input);
}

/** "2026-01-15" → "January 15, 2026" */
export function fmtDateLong(input: string | number | Date): string {
  return toDate(input).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

/** "2026-01-15" → "Jan 15, 2026" */
export function fmtDateMedium(input: string | number | Date): string {
  return toDate(input).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "2026-01-15" → "Jan 2026" */
export function fmtDateMonthYear(iso: string): string {
  return parseLocalDate(iso).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

/** Format a timestamp for chart X-axis ticks: "Jan 26" */
export function fmtTick(ts: number): string {
  return new Date(ts).toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}
