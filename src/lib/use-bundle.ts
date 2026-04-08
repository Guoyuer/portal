"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TIMELINE_URL } from "@/lib/config";
import {
  TimelineDataSchema,
  type AllocationResponse,
  type CashflowResponse,
  type ActivityResponse,
  type MarketData,
  type HoldingsDetailData,
  type DailyPoint,
  type DailyTicker,
  type FidelityTxn,
  type QianjiTxn,
  type PrefixPoint,
  type TimelineData,
  type ApiTicker,
  type ApiCategory,
} from "@/lib/schema";

// ── Target allocation weights (mirror server _CATEGORIES) ───────────────

const CATEGORIES: { name: string; key: keyof DailyPoint; target: number }[] = [
  { name: "US Equity", key: "usEquity", target: 55 },
  { name: "Non-US Equity", key: "nonUsEquity", target: 15 },
  { name: "Crypto", key: "crypto", target: 3 },
  { name: "Safe Net", key: "safeNet", target: 27 },
];

// ── Local computation: allocation ───────────────────────────────────────

function computeAllocation(
  daily: DailyPoint[],
  tickerIndex: Map<string, ApiTicker[]>,
  dateIndex: Map<string, number>,
  date: string,
): AllocationResponse | null {
  const idx = dateIndex.get(date);
  if (idx === undefined) return null;
  const d = daily[idx];
  const total = d.total;
  const liabilities = d.liabilities;

  const categories: ApiCategory[] = CATEGORIES.map(({ name, key, target }) => {
    const value = d[key] as number;
    const pct = total ? Math.round((value / total) * 1000) / 10 : 0;
    return { name, value, pct, target, deviation: Math.round((pct - target) * 10) / 10 };
  });

  const tickers: ApiTicker[] = tickerIndex.get(date) ?? [];

  return { total, netWorth: Math.round((total + liabilities) * 100) / 100, liabilities, categories, tickers };
}

// ── Local computation: cashflow ─────────────────────────────────────────

function computeCashflow(qianjiTxns: QianjiTxn[], start: string, end: string): CashflowResponse {
  const incomeMap = new Map<string, { amount: number; count: number }>();
  const expenseMap = new Map<string, { amount: number; count: number }>();
  let ccPayments = 0;

  for (const t of qianjiTxns) {
    if (t.date < start || t.date > end) continue;
    if (t.type === "income") {
      const e = incomeMap.get(t.category) ?? { amount: 0, count: 0 };
      e.amount += t.amount;
      e.count += 1;
      incomeMap.set(t.category, e);
    } else if (t.type === "expense") {
      const e = expenseMap.get(t.category) ?? { amount: 0, count: 0 };
      e.amount += t.amount;
      e.count += 1;
      expenseMap.set(t.category, e);
    } else if (t.type === "repayment") {
      ccPayments += t.amount;
    }
  }

  const incomeItems = [...incomeMap.entries()]
    .map(([category, v]) => ({ category, amount: Math.round(v.amount * 100) / 100, count: v.count }))
    .sort((a, b) => b.amount - a.amount);
  const expenseItems = [...expenseMap.entries()]
    .map(([category, v]) => ({ category, amount: Math.round(v.amount * 100) / 100, count: v.count }))
    .sort((a, b) => b.amount - a.amount);

  const totalIncome = Math.round(incomeItems.reduce((s, i) => s + i.amount, 0) * 100) / 100;
  const totalExpenses = Math.round(expenseItems.reduce((s, i) => s + i.amount, 0) * 100) / 100;
  const netCashflow = Math.round((totalIncome - totalExpenses) * 100) / 100;
  const savingsRate = totalIncome ? Math.round(((totalIncome - totalExpenses) / totalIncome) * 10000) / 100 : 0;
  const k401 = incomeItems.find((i) => i.category === "401K")?.amount ?? 0;
  const takehomeIncome = totalIncome - k401;
  const takehomeSavingsRate = takehomeIncome ? Math.round(((takehomeIncome - totalExpenses) / takehomeIncome) * 10000) / 100 : 0;

  return { incomeItems, expenseItems, totalIncome, totalExpenses, netCashflow, ccPayments: Math.round(ccPayments * 100) / 100, savingsRate, takehomeSavingsRate };
}

// ── Local computation: reconcile (Fidelity deposits vs Qianji transfers) ──

interface ReconcileResult {
  matched: number;
  unmatchedFidelity: number;
  total: number;
  ok: boolean;
}

/** Convert fidelity "MM/DD/YYYY" to sortable "YYYYMMDD" */
function fidelityDateToSort(runDate: string): string {
  return runDate.slice(6, 10) + runDate.slice(0, 2) + runDate.slice(3, 5);
}

/** Convert fidelity "MM/DD/YYYY" to epoch ms */
function fidelityDateToMs(runDate: string): number {
  return new Date(`${runDate.slice(6, 10)}-${runDate.slice(0, 2)}-${runDate.slice(3, 5)}`).getTime();
}

const ONE_DAY_MS = 86_400_000;

function computeReconcile(fidelityTxns: FidelityTxn[], qianjiTxns: QianjiTxn[], start: string, end: string): ReconcileResult {
  const startSort = start.replaceAll("-", "");
  const endSort = end.replaceAll("-", "");

  // Fidelity EFT deposits in range
  const deposits = fidelityTxns.filter((t) => {
    if (!t.action.startsWith("Electronic Funds Transfer Received")) return false;
    const sort = fidelityDateToSort(t.runDate);
    return sort >= startSort && sort <= endSort;
  });

  // Qianji transfers (no date filter — allow ±1 day outside range)
  const transfers = qianjiTxns.filter((t) => t.type === "transfer");

  // Greedy match: for each deposit, find first unmatched transfer with same amount & date ±1 day
  const used = new Set<number>();
  let matched = 0;

  for (const dep of deposits) {
    const depMs = fidelityDateToMs(dep.runDate);
    const depAmt = Math.round(Math.abs(dep.amount) * 100);

    for (let i = 0; i < transfers.length; i++) {
      if (used.has(i)) continue;
      const tr = transfers[i];
      if (Math.round(tr.amount * 100) !== depAmt) continue;
      const trMs = new Date(tr.date).getTime();
      if (Math.abs(depMs - trMs) <= ONE_DAY_MS) {
        used.add(i);
        matched++;
        break;
      }
    }
  }

  return { matched, unmatchedFidelity: deposits.length - matched, total: deposits.length, ok: deposits.length > 0 && deposits.length === matched };
}

// ── Local computation: activity ─────────────────────────────────────────

function computeActivity(fidelityTxns: FidelityTxn[], start: string, end: string): ActivityResponse {
  const startSort = start.replaceAll("-", "");
  const endSort = end.replaceAll("-", "");

  const buys = new Map<string, { count: number; total: number }>();
  const sells = new Map<string, { count: number; total: number }>();
  const dividends = new Map<string, { count: number; total: number }>();

  for (const t of fidelityTxns) {
    const sort = fidelityDateToSort(t.runDate);
    if (sort < startSort || sort > endSort) continue;
    if (!t.symbol) continue;
    const action = t.action.toUpperCase();
    if (action.startsWith("YOU BOUGHT")) {
      const e = buys.get(t.symbol) ?? { count: 0, total: 0 };
      e.count += 1;
      e.total += Math.abs(t.amount);
      buys.set(t.symbol, e);
    } else if (action.startsWith("YOU SOLD")) {
      const e = sells.get(t.symbol) ?? { count: 0, total: 0 };
      e.count += 1;
      e.total += Math.abs(t.amount);
      sells.set(t.symbol, e);
    } else if (action.startsWith("DIVIDEND")) {
      const e = dividends.get(t.symbol) ?? { count: 0, total: 0 };
      e.count += 1;
      e.total += t.amount;
      dividends.set(t.symbol, e);
    }
  }

  const toList = (m: Map<string, { count: number; total: number }>) =>
    [...m.entries()]
      .map(([symbol, v]) => ({ symbol, count: v.count, total: Math.round(v.total * 100) / 100 }))
      .sort((a, b) => b.total - a.total);

  return { buysBySymbol: toList(buys), sellsBySymbol: toList(sells), dividendsBySymbol: toList(dividends) };
}

// ── Downsampling ────────────────────────────────────────────────────────

const TARGET_CHART_POINTS = 150;

function downsample(daily: DailyPoint[]): { sampled: DailyPoint[]; toFull: number[] } {
  const step = Math.max(1, Math.floor(daily.length / TARGET_CHART_POINTS));
  const sampled: DailyPoint[] = [];
  const toFull: number[] = [];
  for (let i = 0; i < daily.length; i += step) {
    sampled.push(daily[i]);
    toFull.push(i);
  }
  if (toFull[toFull.length - 1] !== daily.length - 1) {
    sampled.push(daily[daily.length - 1]);
    toFull.push(daily.length - 1);
  }
  return { sampled, toFull };
}

// ── Prefix sum range query (O(1)) ──────────────────────────────────────

function prefixRange(prefix: PrefixPoint[], left: number, right: number): PrefixPoint {
  const r = prefix[right];
  const l = left > 0 ? prefix[left - 1] : null;
  return {
    date: r.date,
    income: r.income - (l?.income ?? 0),
    expenses: r.expenses - (l?.expenses ?? 0),
    buys: r.buys - (l?.buys ?? 0),
    sells: r.sells - (l?.sells ?? 0),
    dividends: r.dividends - (l?.dividends ?? 0),
    netCashIn: r.netCashIn - (l?.netCashIn ?? 0),
    ccPayments: r.ccPayments - (l?.ccPayments ?? 0),
  };
}

// ── Build indexes ──────────────────────────────────────────────────────

function buildDateIndex(daily: DailyPoint[]): Map<string, number> {
  const m = new Map<string, number>();
  for (let i = 0; i < daily.length; i++) m.set(daily[i].date, i);
  return m;
}

function buildTickerIndex(tickers: DailyTicker[]): Map<string, ApiTicker[]> {
  const m = new Map<string, ApiTicker[]>();
  for (const t of tickers) {
    let arr = m.get(t.date);
    if (!arr) { arr = []; m.set(t.date, arr); }
    arr.push({
      ticker: t.ticker,
      value: t.value,
      category: t.category,
      subtype: t.subtype,
      costBasis: t.costBasis,
      gainLoss: t.gainLoss,
      gainLossPct: t.gainLossPct,
    });
  }
  return m;
}

// ── Hook ────────────────────────────────────────────────────────────────

export interface BundleState {
  // Timeline (same as old useTimeline)
  chartDaily: DailyPoint[];
  prefix: PrefixPoint[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  snapshot: DailyPoint | null;
  range: PrefixPoint | null;
  startDate: string | null;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
  // Computed data (instant, no network)
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  market: MarketData | null;
  holdingsDetail: HoldingsDetailData | null;
  reconcile: ReconcileResult | null;
}

export function useBundle(): BundleState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [fullRange, setFullRange] = useState({ start: 0, end: 0 });
  const brushRef = useRef({ start: 0, end: 0 });
  const rafRef = useRef(0);

  // ── Fetch once ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(TIMELINE_URL, { cache: "no-store", signal: AbortSignal.timeout(10000) });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const json = await res.json();
        const parsed = TimelineDataSchema.safeParse(json);
        if (!parsed.success) throw new Error("Invalid timeline data");
        if (!cancelled) setData(parsed.data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // ── Indexes (built once when data arrives) ──────────────────────────
  const dateIndex = useMemo(() => data ? buildDateIndex(data.daily) : new Map<string, number>(), [data]);
  const tickerIndex = useMemo(() => data ? buildTickerIndex(data.dailyTickers) : new Map(), [data]);

  // ── Downsampling ────────────────────────────────────────────────────
  const { sampled: chartDaily, toFull } = useMemo(
    () => data ? downsample(data.daily) : { sampled: [] as DailyPoint[], toFull: [] as number[] },
    [data],
  );

  const defaultEndIndex = chartDaily.length > 0 ? chartDaily.length - 1 : 0;
  const defaultStartIndex = useMemo(() => {
    if (!data || chartDaily.length === 0) return 0;
    const step = Math.max(1, Math.floor(data.daily.length / TARGET_CHART_POINTS));
    return Math.max(0, defaultEndIndex - Math.floor(252 / step));
  }, [data, chartDaily.length, defaultEndIndex]);

  useEffect(() => {
    if (data && toFull.length > 0) {
      const s = toFull[defaultStartIndex] ?? 0;
      const e = toFull[defaultEndIndex] ?? 0;
      brushRef.current = { start: defaultStartIndex, end: defaultEndIndex };
      setFullRange({ start: s, end: e });
    }
  }, [data, toFull, defaultStartIndex, defaultEndIndex]);

  const onBrushChange = useCallback((state: { startIndex?: number; endIndex?: number }) => {
    if (state.startIndex !== undefined) brushRef.current.start = state.startIndex;
    if (state.endIndex !== undefined) brushRef.current.end = state.endIndex;
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      setFullRange({
        start: toFull[brushRef.current.start] ?? 0,
        end: toFull[brushRef.current.end] ?? 0,
      });
    });
  }, [toFull]);

  // ── Derived timeline state (instant — user sees these during drag) ──
  const snapshot = useMemo(() => data?.daily[fullRange.end] ?? null, [data, fullRange.end]);
  const range = useMemo(() => data ? prefixRange(data.prefix, fullRange.start, fullRange.end) : null, [data, fullRange.start, fullRange.end]);
  const startDate = data?.daily[fullRange.start]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;

  // ── Computed data (pure, instant) ───────────────────────────────────
  const allocation = useMemo(
    () => (data && snapshotDate) ? computeAllocation(data.daily, tickerIndex, dateIndex, snapshotDate) : null,
    [data, snapshotDate, tickerIndex, dateIndex],
  );

  const cashflow = useMemo(
    () => (data && startDate && snapshotDate) ? computeCashflow(data.qianjiTxns, startDate, snapshotDate) : null,
    [data, startDate, snapshotDate],
  );

  const activity = useMemo(
    () => (data && startDate && snapshotDate) ? computeActivity(data.fidelityTxns, startDate, snapshotDate) : null,
    [data, startDate, snapshotDate],
  );

  const reconcile = useMemo(
    () => (data && startDate && snapshotDate) ? computeReconcile(data.fidelityTxns, data.qianjiTxns, startDate, snapshotDate) : null,
    [data, startDate, snapshotDate],
  );

  return {
    chartDaily,
    prefix: data?.prefix ?? [],
    defaultStartIndex,
    defaultEndIndex,
    snapshot,
    range,
    startDate,
    onBrushChange,
    loading,
    error,
    allocation,
    cashflow,
    activity,
    market: data?.market ?? null,
    holdingsDetail: data?.holdingsDetail ?? null,
    reconcile,
  };
}
