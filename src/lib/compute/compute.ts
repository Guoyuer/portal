// ── Pure computation functions (no React dependencies) ────────────────────

import type {
  CategoryMeta,
  DailyPoint,
  DailyTicker,
  FidelityTxn,
  QianjiTxn,
} from "@/lib/schemas";
import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  ApiTicker,
  ApiCategory,
  MonthlyFlowPoint,
} from "@/lib/compute/computed-types";

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

// ── Category colour palette ──────────────────────────────────────────────
import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";

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
  // Track retirement income separately so the take-home calculation is
  // independent of category display names.
  let retirementIncome = 0;

  for (const t of qianjiTxns) {
    if (t.date < start || t.date > end) continue;
    if (t.type === "income") {
      accum(incomeMap, t.category, t.amount);
      if (t.isRetirement) retirementIncome += t.amount;
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
  const takehomeIncome = totalIncome - round(retirementIncome);
  const takehomeSavingsRate = takehomeIncome ? round(((takehomeIncome - totalExpenses) / takehomeIncome) * 100) : 0;

  return { incomeItems, expenseItems, totalIncome, totalExpenses, netCashflow, ccPayments: round(ccPayments), savingsRate, takehomeSavingsRate };
}

/** UI-side cashflow state: distinguishes bundle failure, no-data window, and real data. */
export type CashflowState =
  | { kind: "unavailable" }
  | { kind: "empty" }
  | { kind: "data"; data: CashflowResponse };

export function cashflowState(cashflow: CashflowResponse | null): CashflowState {
  if (!cashflow) return { kind: "unavailable" };
  if (cashflow.totalIncome === 0 && cashflow.totalExpenses === 0) return { kind: "empty" };
  return { kind: "data", data: cashflow };
}

// ── Cross-check (Fidelity deposits vs Qianji transfers) ─────────────────

// Fidelity runDate is ISO YYYY-MM-DD (normalized at the pipeline ingestion
// boundary) so string comparison and `new Date(...)` both just work.

const MATCH_WINDOW_MS = 7 * 86_400_000; // Qianji can lag Fidelity by up to 7 days
// Sub-dollar Fidelity "deposits" are cash-sweep dust / residual interest,
// not funded transfers the user would ever log in Qianji. Exclude them so
// the ✗ count reflects deposits the user actually made.
const DUST_THRESHOLD = 1;

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
  // Qianji data has a historical floor — the user started using it partway
  // through the Fidelity history. Deposits before that floor are structurally
  // unmatchable (no Qianji ledger exists to cross-reference). Give the floor
  // one match window of grace so that a deposit whose matching transfer is
  // itself the earliest Qianji entry still counts.
  let earliestQianji: string | null = null;
  for (const t of qianjiTxns) {
    if (earliestQianji === null || t.date < earliestQianji) earliestQianji = t.date;
  }
  let effectiveStart = start;
  if (earliestQianji) {
    const floorMs = new Date(earliestQianji).getTime() - MATCH_WINDOW_MS;
    const floor = new Date(floorMs).toISOString().slice(0, 10);
    if (floor > effectiveStart) effectiveStart = floor;
  }

  const deposits: { amt: number; ms: number }[] = [];
  let fidelityTotal = 0;
  for (const t of fidelityTxns) {
    if (t.actionType !== "deposit") continue;
    if (Math.abs(t.amount) < DUST_THRESHOLD) continue;
    if (t.runDate >= effectiveStart && t.runDate <= end) {
      deposits.push({ amt: Math.round(Math.abs(t.amount) * 100), ms: new Date(t.runDate).getTime() });
      fidelityTotal += t.amount;
    }
  }
  fidelityTotal = round(fidelityTotal);

  // Candidate Qianji rows to match against:
  // - transfers (user moves money into Fidelity from another account), and
  // - income booked directly into a Fidelity account (payroll direct deposit,
  //   rebate rewards). Qianji logs those as ``type=income`` with
  //   ``accountTo="Fidelity …"`` rather than as a transfer. Matching on the
  //   accountTo prefix (case-insensitive) covers "Fidelity taxable",
  //   "Fidelity Roth IRA", etc. without having to enumerate every account.
  const candidates = qianjiTxns.filter(
    (t) =>
      t.type === "transfer" ||
      (t.type === "income" && t.accountTo.toLowerCase().startsWith("fidelity")),
  );
  const used = new Set<number>();
  let matchedCount = 0;
  let matchedTotal = 0;

  for (const dep of deposits) {
    let bestIdx = -1;
    let bestDist = Infinity;
    for (let i = 0; i < candidates.length; i++) {
      if (used.has(i)) continue;
      if (Math.round(candidates[i].amount * 100) !== dep.amt) continue;
      const dist = Math.abs(dep.ms - new Date(candidates[i].date).getTime());
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

// ── Group-aware activity ──────────────────────────────────────────────────

import { groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS, groupOfTicker } from "@/lib/config/equivalent-groups";

export type ActivityRow = {
  symbol: string;
  count: number;
  total: number;
  isGroup?: boolean;
  groupKey?: string;
};

export type GroupedActivityResponse = {
  buysBySymbol: ActivityRow[];
  sellsBySymbol: ActivityRow[];
  dividendsBySymbol: ActivityRow[];
};

export function computeGroupedActivity(
  fidelityTxns: FidelityTxn[],
  start: string,
  end: string,
): GroupedActivityResponse {
  // Window the txns first
  const windowed = fidelityTxns.filter((t) => t.runDate >= start && t.runDate <= end && t.symbol);

  // Group markers via the shared algorithm
  const groupMarkers = groupNetByDate(windowed);
  const groupBuys: ActivityRow[] = [];
  const groupSells: ActivityRow[] = [];
  for (const [groupKey, byDate] of groupMarkers) {
    const display = EQUIVALENT_GROUPS[groupKey].display;
    let buyTotal = 0, buyCount = 0, sellTotal = 0, sellCount = 0;
    for (const entry of byDate.values()) {
      if (entry.side === "buy") { buyTotal += entry.net; buyCount += 1; }
      else                      { sellTotal += entry.net; sellCount += 1; }
    }
    if (buyCount > 0)  groupBuys.push({ symbol: display, count: buyCount, total: round(buyTotal), isGroup: true, groupKey });
    if (sellCount > 0) groupSells.push({ symbol: display, count: sellCount, total: round(sellTotal), isGroup: true, groupKey });
  }

  // Solo tickers (not in any group) — reuse computeActivity for the B/S rows
  const solo = windowed.filter((t) => !groupOfTicker(t.symbol));
  const soloActivity = computeActivity(solo, start, end);

  // Dividends stay per-ticker across all tickers (grouping out of scope).
  // Compute inline over `windowed` to avoid a second computeActivity pass
  // that would rebuild the full buys/sells Maps just to be discarded.
  const dividends = new Map<string, { count: number; total: number }>();
  for (const t of windowed) {
    const abs = Math.abs(t.amount);
    if (t.actionType === "dividend") accum(dividends, t.symbol, t.amount);
    else if (t.actionType === "reinvestment") accum(dividends, t.symbol, abs);
  }

  const sortDesc = (a: ActivityRow, b: ActivityRow) => b.total - a.total;
  return {
    buysBySymbol:  [...groupBuys,  ...soloActivity.buysBySymbol.map(r => ({ ...r, isGroup: false as const }))].sort(sortDesc),
    sellsBySymbol: [...groupSells, ...soloActivity.sellsBySymbol.map(r => ({ ...r, isGroup: false as const }))].sort(sortDesc),
    dividendsBySymbol: [...dividends.entries()]
      .map(([symbol, v]) => ({ symbol, count: v.count, total: round(v.total), isGroup: false as const }))
      .sort(sortDesc),
  };
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
