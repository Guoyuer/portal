// ── Pure computation functions (no React dependencies) ────────────────────

import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  CategoryMeta,
  DailyPoint,
  DailyTicker,
  FidelityTxn,
  QianjiTxn,
  ApiTicker,
  ApiCategory,
  MonthlyFlowPoint,
} from "@/lib/schema";

// ── Helpers ──────────────────────────────────────────────────────────────

function round(val: number, decimals = 2): number {
  const factor = 10 ** decimals;
  return Math.round(val * factor) / factor;
}

function accum(map: Map<string, { count: number; total: number }>, key: string, amount: number) {
  const e = map.get(key) ?? { count: 0, total: 0 };
  e.count += 1;
  e.total += amount;
  map.set(key, e);
}

// ── Category colour palette (frontend-only — accessibility concern) ──────
// Keyed on the camelCase category `key` from the bundle. Pipeline owns the
// name/order/target; colours stay here so the palette can be tuned without
// re-syncing D1.
// Okabe-Ito colorblind-friendly palette (protanomaly-safe).
export const CAT_COLOR_BY_KEY: Record<string, string> = {
  usEquity: "#0072B2",
  nonUsEquity: "#009E73",
  crypto: "#E69F00",
  safeNet: "#56B4E9",
};

/** Build a display-name → color map from the bundle's categories. */
export function catColorByName(categories: CategoryMeta[]): Record<string, string> {
  return Object.fromEntries(
    categories.map((c) => [c.name, CAT_COLOR_BY_KEY[c.key] ?? "#888888"]),
  );
}

// ── Allocation ────────────────────────────────────────────────────────────

export function computeAllocation(
  daily: DailyPoint[],
  tickerIndex: Map<string, ApiTicker[]>,
  dateIndex: Map<string, number>,
  date: string,
  categories: CategoryMeta[],
): AllocationResponse | null {
  const idx = dateIndex.get(date);
  if (idx === undefined) return null;
  const d = daily[idx];
  const total = d.total;
  const liabilities = d.liabilities;

  const apiCategories: ApiCategory[] = categories.map(({ name, key, targetPct }) => {
    const value = (d[key as keyof DailyPoint] as number | undefined) ?? 0;
    const pct = total ? round((value / total) * 100, 1) : 0;
    return { name, value, pct, target: targetPct, deviation: round(pct - targetPct, 1) };
  });

  const tickers: ApiTicker[] = tickerIndex.get(date) ?? [];

  return { total, netWorth: round(total + liabilities), liabilities, categories: apiCategories, tickers };
}

// ── Cashflow ──────────────────────────────────────────────────────────────

export function computeCashflow(qianjiTxns: QianjiTxn[], start: string, end: string): CashflowResponse {
  const incomeMap = new Map<string, { count: number; total: number }>();
  const expenseMap = new Map<string, { count: number; total: number }>();
  let ccPayments = 0;

  for (const t of qianjiTxns) {
    if (t.date < start || t.date > end) continue;
    if (t.type === "income") {
      accum(incomeMap, t.category, t.amount);
    } else if (t.type === "expense") {
      accum(expenseMap, t.category, t.amount);
    } else if (t.type === "repayment") {
      ccPayments += t.amount;
    }
  }

  const toItems = (m: Map<string, { count: number; total: number }>) =>
    [...m.entries()]
      .map(([category, v]) => ({ category, amount: round(v.total), count: v.count }))
      .sort((a, b) => b.amount - a.amount);

  const incomeItems = toItems(incomeMap);
  const expenseItems = toItems(expenseMap);

  const totalIncome = round(incomeItems.reduce((s, i) => s + i.amount, 0));
  const totalExpenses = round(expenseItems.reduce((s, i) => s + i.amount, 0));
  const netCashflow = round(totalIncome - totalExpenses);
  const savingsRate = totalIncome ? round(((totalIncome - totalExpenses) / totalIncome) * 100) : 0;
  const k401 = incomeItems.find((i) => i.category.toLowerCase().includes("401"))?.amount ?? 0;
  const takehomeIncome = totalIncome - k401;
  const takehomeSavingsRate = takehomeIncome ? round(((takehomeIncome - totalExpenses) / takehomeIncome) * 100) : 0;

  return { incomeItems, expenseItems, totalIncome, totalExpenses, netCashflow, ccPayments: round(ccPayments), savingsRate, takehomeSavingsRate };
}

// ── Cross-check (Fidelity deposits vs Qianji transfers) ─────────────────

// Fidelity runDate is ISO YYYY-MM-DD (normalized at the pipeline ingestion
// boundary) so string comparison and `new Date(...)` both just work.

export const MATCH_WINDOW_MS = 7 * 86_400_000; // Qianji can lag Fidelity by up to 7 days

export interface CrossCheck {
  fidelityTotal: number;
  matchedTotal: number;
  unmatchedTotal: number;
  matchedCount: number;
  totalCount: number;
  ok: boolean;
}

export function computeCrossCheck(
  fidelityTxns: FidelityTxn[],
  qianjiTxns: QianjiTxn[],
  start: string,
  end: string,
): CrossCheck {
  const deposits: { amt: number; ms: number }[] = [];
  let fidelityTotal = 0;
  for (const t of fidelityTxns) {
    if (t.actionType !== "deposit") continue;
    if (t.runDate >= start && t.runDate <= end) {
      deposits.push({ amt: Math.round(Math.abs(t.amount) * 100), ms: new Date(t.runDate).getTime() });
      fidelityTotal += t.amount;
    }
  }
  fidelityTotal = round(fidelityTotal);

  const transfers = qianjiTxns.filter((t) => t.type === "transfer");
  const used = new Set<number>();
  let matchedCount = 0;
  let matchedTotal = 0;

  for (const dep of deposits) {
    let bestIdx = -1;
    let bestDist = Infinity;
    for (let i = 0; i < transfers.length; i++) {
      if (used.has(i)) continue;
      if (Math.round(transfers[i].amount * 100) !== dep.amt) continue;
      const dist = Math.abs(dep.ms - new Date(transfers[i].date).getTime());
      if (dist <= MATCH_WINDOW_MS && dist < bestDist) {
        bestIdx = i;
        bestDist = dist;
      }
    }
    if (bestIdx >= 0) {
      used.add(bestIdx);
      matchedCount++;
      matchedTotal += dep.amt / 100;
    }
  }

  matchedTotal = round(matchedTotal);
  const unmatchedTotal = round(fidelityTotal - matchedTotal);

  return {
    fidelityTotal,
    matchedTotal,
    unmatchedTotal,
    matchedCount,
    totalCount: deposits.length,
    ok: deposits.length > 0 && matchedCount === deposits.length,
  };
}

// ── Activity ──────────────────────────────────────────────────────────────

export function computeActivity(fidelityTxns: FidelityTxn[], start: string, end: string): ActivityResponse {
  const buys = new Map<string, { count: number; total: number }>();
  const sells = new Map<string, { count: number; total: number }>();
  const dividends = new Map<string, { count: number; total: number }>();

  for (const t of fidelityTxns) {
    if (t.runDate < start || t.runDate > end) continue;
    if (!t.symbol) continue;
    const abs = Math.abs(t.amount);
    if (t.actionType === "buy") {
      accum(buys, t.symbol, abs);
    } else if (t.actionType === "sell") {
      accum(sells, t.symbol, abs);
    } else if (t.actionType === "dividend") {
      accum(dividends, t.symbol, t.amount);
    } else if (t.actionType === "reinvestment") {
      accum(dividends, t.symbol, abs);
      accum(buys, t.symbol, abs);
    }
  }

  const toList = (m: Map<string, { count: number; total: number }>) =>
    [...m.entries()]
      .map(([symbol, v]) => ({ symbol, count: v.count, total: round(v.total) }))
      .sort((a, b) => b.total - a.total);

  return { buysBySymbol: toList(buys), sellsBySymbol: toList(sells), dividendsBySymbol: toList(dividends) };
}

// ── Build indexes ─────────────────────────────────────────────────────────

export function buildDateIndex(daily: DailyPoint[]): Map<string, number> {
  const m = new Map<string, number>();
  for (let i = 0; i < daily.length; i++) m.set(daily[i].date, i);
  return m;
}

export function buildTickerIndex(tickers: DailyTicker[]): Map<string, ApiTicker[]> {
  const m = new Map<string, ApiTicker[]>();
  for (const { date, ...rest } of tickers) {
    let arr = m.get(date);
    if (!arr) { arr = []; m.set(date, arr); }
    arr.push(rest);
  }
  return m;
}

// ── Monthly flows (from finance/page.tsx) ─────────────────────────────────

export function computeMonthlyFlows(qianjiTxns: QianjiTxn[], start: string | null, end: string | null): MonthlyFlowPoint[] {
  if (!qianjiTxns.length || !start || !end) return [];

  const months = new Map<string, { income: number; expenses: number }>();

  for (const t of qianjiTxns) {
    if (t.date < start || t.date > end) continue;
    if (t.type !== "income" && t.type !== "expense") continue;
    const month = t.date.slice(0, 7);
    const entry = months.get(month) ?? { income: 0, expenses: 0 };
    if (t.type === "income") entry.income += t.amount;
    else entry.expenses += t.amount;
    months.set(month, entry);
  }

  return Array.from(months.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, { income, expenses }]) => ({
      month,
      income: round(income),
      expenses: round(expenses),
      savingsRate: income > 0 ? round(((income - expenses) / income) * 100) : 0,
    }));
}
