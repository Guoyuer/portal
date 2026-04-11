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

// ── Date formatting (timezone-safe — avoids UTC parse of "YYYY-MM-DD") ──

/** "2026-01-15" → "January 15, 2026" */
export function fmtDateLong(iso: string): string {
  const [y, m, d] = iso.split("-");
  return new Date(+y, +m - 1, +d).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

/** "2026-01-15" → "Jan 15, 2026" */
export function fmtDateMedium(iso: string): string {
  const [y, m, d] = iso.split("-");
  return new Date(+y, +m - 1, +d).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
}

/** "2026-01-15" → "Jan 2026" */
export function fmtDateMonthYear(iso: string): string {
  const [y, m] = iso.split("-");
  return new Date(+y, +m - 1, 1).toLocaleDateString("en-US", { month: "short", year: "numeric" });
}
