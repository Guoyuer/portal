export function fmtCurrency(val: number): string {
  const abs = Math.abs(val);
  const decimals = abs < 10 ? 2 : 0;
  const formatted = abs.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return val < 0 ? `-$${formatted}` : `$${formatted}`;
}

export function fmtCurrencyShort(val: number): string {
  if (val >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (val >= 1_000) return `$${(val / 1_000).toFixed(0)}k`;
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
