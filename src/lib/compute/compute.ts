// ── Pure computation functions (no React dependencies) ────────────────────

import type {
  CategoryMeta,
  DailyPoint,
  DailyTicker,
  FidelityTxn,
  QianjiTxn,
  RobinhoodTxn,
  EmpowerContribution,
} from "@/lib/schemas/timeline";
import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  ActivityTicker,
  ApiTicker,
  ApiCategory,
  MonthlyFlowPoint,
  SourceKind,
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
import { parseLocalDate } from "@/lib/format/format";

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

  const apiCategories: ApiCategory[] = categories.map(({ name, key, targetPct }) => {
    const value = (d[key as keyof DailyPoint] as number | undefined) ?? 0;
    const pct = total ? round((value / total) * 100, 1) : 0;
    return { name, value, pct, target: targetPct };
  });

  const tickers: ApiTicker[] = tickerIndex.get(date) ?? [];

  return { total, netWorth: round(total + d.liabilities), categories: apiCategories, tickers };
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

// ── Investment txn unification ────────────────────────────────────────────

/** Unified shape used by computeActivity + computeCrossCheck. Internal to
 *  the compute layer; does NOT cross the artifact/Worker/Zod boundary. */
const INVESTMENT_ACTION_TYPES = [
  "buy", "sell", "dividend", "reinvestment", "deposit", "contribution",
] as const;
type InvestmentActionType = (typeof INVESTMENT_ACTION_TYPES)[number];

function isInvestmentAction(s: string): s is InvestmentActionType {
  return (INVESTMENT_ACTION_TYPES as readonly string[]).includes(s);
}

export interface InvestmentTxn {
  source: SourceKind;
  date: string;
  ticker: string;
  actionType: InvestmentActionType;
  amount: number;
  quantity: number;
  price: number;
}

export function normalizeInvestmentTxns(
  fidelity: FidelityTxn[],
  robinhood: RobinhoodTxn[],
  empower: EmpowerContribution[],
): InvestmentTxn[] {
  const out: InvestmentTxn[] = [];
  for (const f of fidelity) {
    // Fidelity emits action types outside our union (interest, ira_contribution,
    // distribution, ...); downstream logic only branches on the six above.
    if (!isInvestmentAction(f.actionType)) continue;
    out.push({
      source: "fidelity",
      date: f.runDate,
      ticker: f.symbol,
      actionType: f.actionType,
      amount: f.amount,
      quantity: f.quantity,
      price: f.price,
    });
  }
  for (const r of robinhood) {
    if (!isInvestmentAction(r.actionKind)) continue;
    const absQty = Math.abs(r.quantity);
    out.push({
      source: "robinhood",
      date: r.txnDate,
      ticker: r.ticker,
      actionType: r.actionKind,
      amount: r.amountUsd,
      quantity: r.quantity,
      price: absQty > 0 ? Math.abs(r.amountUsd) / absQty : 0,
    });
  }
  for (const e of empower) {
    out.push({
      source: "401k",
      date: e.date,
      ticker: e.ticker,
      actionType: "contribution",
      amount: e.amount,
      quantity: 0,
      price: 0,
    });
  }
  return out;
}

// ── Cross-check (Fidelity + Robinhood deposits vs Qianji transfers) ─────

// Deposit dates are ISO YYYY-MM-DD (normalized at the pipeline ingestion
// boundary) so string comparison and `new Date(...)` both just work.

const MATCH_WINDOW_MS = 7 * 86_400_000; // Qianji can lag a deposit by up to 7 days
// Sub-dollar deposits are cash-sweep dust / residual interest,
// not funded transfers the user would ever log in Qianji. Exclude them so
// the ✗ count reflects deposits the user actually made.
const DUST_THRESHOLD = 1;

export interface UnmatchedItem {
  source: Exclude<SourceKind, "401k">;
  date: string;
  amount: number;
}

interface SourceCrossCheck {
  matched: number;
  total: number;
  unmatched: UnmatchedItem[];
}

const CROSS_CHECK_SOURCES = [
  { source: "fidelity", accountPrefix: "fidelity" },
  { source: "robinhood", accountPrefix: "robinhood" },
] as const satisfies readonly { source: UnmatchedItem["source"]; accountPrefix: string }[];

function emptySourceCrossCheck(): SourceCrossCheck {
  return { matched: 0, total: 0, unmatched: [] };
}

export interface CrossCheck {
  matchedCount: number;
  totalCount: number;
  ok: boolean;
  perSource: {
    fidelity:  SourceCrossCheck;
    robinhood: SourceCrossCheck;
  };
  allUnmatched: UnmatchedItem[];
}

export function computeCrossCheck(
  investmentTxns: InvestmentTxn[],
  qianjiTxns: QianjiTxn[],
  start: string,
  end: string,
): CrossCheck {
  // Qianji data has a historical floor — the user started using it partway
  // through the investment history. Deposits before that floor are structurally
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

  const perSource: CrossCheck["perSource"] = {
    fidelity: emptySourceCrossCheck(),
    robinhood: emptySourceCrossCheck(),
  };

  // Candidates: Qianji transfers, or income booked directly into the broker
  // account (payroll direct deposit, rebate rewards).
  for (const { source, accountPrefix } of CROSS_CHECK_SOURCES) {
    const deposits = investmentTxns
      .filter((t) => t.source === source && t.actionType === "deposit"
        && Math.abs(t.amount) >= DUST_THRESHOLD
        && t.date >= effectiveStart && t.date <= end)
      .map((t) => ({ amount: Math.abs(t.amount), ms: new Date(t.date).getTime(), date: t.date }));
    const candidates = qianjiTxns.filter((q) =>
      q.type === "transfer" ||
      (q.type === "income" && q.accountTo.toLowerCase().startsWith(accountPrefix)),
    );
    matchAndRecord(deposits, candidates, perSource[source], source);
  }

  // 401k contributions are excluded — the pipeline reconciles QFX vs Qianji
  // at ingest time (ContributionReconcileError on per-date $1 mismatch);
  // a UI cross-check layer would be ~100% tautological.

  const matchedCount = perSource.fidelity.matched + perSource.robinhood.matched;
  const totalCount   = perSource.fidelity.total   + perSource.robinhood.total;
  const allUnmatched = [...perSource.fidelity.unmatched, ...perSource.robinhood.unmatched];

  return {
    matchedCount,
    totalCount,
    ok: totalCount > 0 && matchedCount === totalCount,
    perSource,
    allUnmatched,
  };
}

// Helper: earliest-in-window matching (bipartite matching on an interval graph).
// Processing deposits chronologically and picking the earliest in-window
// candidate each time is provably maximum-matching for this class of graph,
// unlike "nearest unused" greedy which can orphan deposits when an earlier
// deposit steals the only candidate a later one also needs.
function matchAndRecord(
  deposits: Array<{ amount: number; ms: number; date: string }>,
  candidates: QianjiTxn[],
  out: SourceCrossCheck,
  sourceLabel: UnmatchedItem["source"],
): void {
  // Pre-compute candidate timestamps once (O(n+m) instead of O(n*m))
  const candidateMs = candidates.map((q) => parseLocalDate(q.date).getTime());
  const used = new Set<number>();
  const sorted = [...deposits].sort((a, b) => a.ms - b.ms);
  for (const dep of sorted) {
    out.total += 1;
    let bestIdx = -1, bestMs = Infinity;
    const depCents = Math.round(dep.amount * 100);
    for (let i = 0; i < candidates.length; i++) {
      if (used.has(i)) continue;
      if (Math.round(candidates[i].amount * 100) !== depCents) continue;
      const candMs = candidateMs[i];
      if (Math.abs(dep.ms - candMs) <= MATCH_WINDOW_MS && candMs < bestMs) {
        bestIdx = i;
        bestMs = candMs;
      }
    }
    if (bestIdx >= 0) {
      used.add(bestIdx);
      out.matched += 1;
    } else {
      out.unmatched.push({ source: sourceLabel, date: dep.date, amount: dep.amount });
    }
  }
}

// ── Activity ──────────────────────────────────────────────────────────────

type ActivityBucket = { count: number; total: number; sources: Set<SourceKind> };

function accumWithSrc(m: Map<string, ActivityBucket>, key: string, amount: number, src: SourceKind) {
  const e = m.get(key) ?? { count: 0, total: 0, sources: new Set<SourceKind>() };
  e.count += 1;
  e.total += amount;
  e.sources.add(src);
  m.set(key, e);
}

export function computeActivity(investmentTxns: InvestmentTxn[], start: string, end: string): ActivityResponse {
  const buys = new Map<string, ActivityBucket>();
  const sells = new Map<string, ActivityBucket>();
  const dividends = new Map<string, ActivityBucket>();

  for (const t of investmentTxns) {
    if (t.date < start || t.date > end) continue;
    if (!t.ticker) continue;
    const abs = Math.abs(t.amount);
    if (t.actionType === "buy" || t.actionType === "contribution") {
      accumWithSrc(buys, t.ticker, abs, t.source);
    } else if (t.actionType === "sell") {
      accumWithSrc(sells, t.ticker, abs, t.source);
    } else if (t.actionType === "dividend") {
      accumWithSrc(dividends, t.ticker, t.amount, t.source);
    } else if (t.actionType === "reinvestment") {
      accumWithSrc(dividends, t.ticker, abs, t.source);
      accumWithSrc(buys, t.ticker, abs, t.source);
    }
    // "deposit" is cross-check territory, not activity
  }

  const toList = (m: Map<string, ActivityBucket>): ActivityTicker[] =>
    [...m.entries()]
      .map(([ticker, v]) => ({
        ticker,
        count: v.count,
        total: round(v.total),
        sources: [...v.sources],
      }))
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

import { EQUIVALENT_GROUPS, groupOfTicker } from "@/lib/data/equivalent-groups";

type GroupAccum = { count: number; total: number; sources: Set<SourceKind>; groupKey?: string };

function foldIntoGroups(rows: ActivityTicker[]): ActivityTicker[] {
  const grouped = new Map<string, GroupAccum>();
  for (const row of rows) {
    const gKey = groupOfTicker(row.ticker);
    const display = gKey ? EQUIVALENT_GROUPS[gKey].display : row.ticker;
    const existing = grouped.get(display);
    if (existing) {
      existing.count += row.count;
      existing.total += row.total;
      for (const s of row.sources) existing.sources.add(s);
    } else {
      grouped.set(display, {
        count: row.count,
        total: row.total,
        sources: new Set(row.sources),
        groupKey: gKey ?? undefined,
      });
    }
  }
  return [...grouped.entries()]
    .map(([ticker, v]) => ({
      ticker,
      count: v.count,
      total: round(v.total),
      groupKey: v.groupKey,
      sources: [...v.sources],
    }))
    .sort((a, b) => b.total - a.total);
}

export function computeGroupedActivity(
  investmentTxns: InvestmentTxn[],
  start: string,
  end: string,
): ActivityResponse {
  const raw = computeActivity(investmentTxns, start, end);
  return {
    buysBySymbol: foldIntoGroups(raw.buysBySymbol),
    sellsBySymbol: foldIntoGroups(raw.sellsBySymbol),
    // Dividends stay per-ticker (grouping out of scope).
    dividendsBySymbol: raw.dividendsBySymbol,
  };
}

// ── Monthly flows ────────────────────────────────────────────────────────

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
    .filter(([, { income }]) => income > 0)
    .map(([month, { income, expenses }]) => ({
      month,
      income: round(income),
      expenses: round(expenses),
      savings: round(Math.max(0, income - expenses)),
      savingsRate: round(((income - expenses) / income) * 100),
    }));
}
