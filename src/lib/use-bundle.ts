"use client";

import { useEffect, useRef, useState } from "react";
import { TIMELINE_URL } from "@/lib/config";
import {
  TimelineDataSchema,
  type AllocationResponse,
  type CashflowResponse,
  type ActivityResponse,
  type MarketData,
  type StockDetail,
  type DailyPoint,
  type QianjiTxn,
  type FidelityTxn,
  type TimelineData,
} from "@/lib/schema";
import {
  computeAllocation,
  computeCashflow,
  computeActivity,
  computeCrossCheck,
  computeMonthlyFlows,
  buildDateIndex,
  buildTickerIndex,
  type CrossCheck,
} from "@/lib/compute";
import type { MonthlyFlowPoint } from "@/lib/schema";

const FETCH_TIMEOUT_MS = 10_000;

// ── Hook ────────────────────────────────────────────────────────────────

export type { CrossCheck };

export interface BundleState {
  chartDaily: DailyPoint[];
  qianjiTxns: QianjiTxn[];
  fidelityTxns: FidelityTxn[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  snapshot: DailyPoint | null;
  startDate: string | null;
  brushStart: number;
  brushEnd: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  market: MarketData | null;
  holdingsDetail: StockDetail[] | null;
  crossCheck: CrossCheck | null;
  monthlyFlows: MonthlyFlowPoint[];
  syncMeta: Record<string, string> | null;
}

export function useBundle(): BundleState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [fullRange, setFullRange] = useState({ start: 0, end: 0 });
  const brushRef = useRef({ start: 0, end: 0 });

  // ── Fetch once ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(TIMELINE_URL, { cache: "no-store", signal: AbortSignal.timeout(FETCH_TIMEOUT_MS) });
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
  const dateIndex = data ? buildDateIndex(data.daily) : new Map<string, number>();
  const tickerIndex = data ? buildTickerIndex(data.dailyTickers) : new Map();

  // ── Chart data (no downsampling — show every day) ───────────────────
  const chartDaily = data?.daily ?? [];

  const defaultEndIndex = chartDaily.length > 0 ? chartDaily.length - 1 : 0;
  const TRADING_DAYS_PER_YEAR = 252;
  const defaultStartIndex = (!data || chartDaily.length === 0)
    ? 0
    : Math.max(0, defaultEndIndex - TRADING_DAYS_PER_YEAR);

  useEffect(() => {
    if (data && chartDaily.length > 0) {
      brushRef.current = { start: defaultStartIndex, end: defaultEndIndex };
      setFullRange({ start: defaultStartIndex, end: defaultEndIndex });
    }
  }, [data, chartDaily.length, defaultStartIndex, defaultEndIndex]);

  const onBrushChange = (state: { startIndex?: number; endIndex?: number }) => {
    if (state.startIndex !== undefined) brushRef.current.start = state.startIndex;
    if (state.endIndex !== undefined) brushRef.current.end = state.endIndex;
    setFullRange({
      start: brushRef.current.start,
      end: brushRef.current.end,
    });
  };

  // ── Derived timeline state (instant — user sees these during drag) ──
  const snapshot = data?.daily[fullRange.end] ?? null;
  const startDate = data?.daily[fullRange.start]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;

  // ── Computed data (pure, instant) ───────────────────────────────────
  const allocation = (data && snapshotDate) ? computeAllocation(data.daily, tickerIndex, dateIndex, snapshotDate) : null;
  const cashflow = (data && startDate && snapshotDate) ? computeCashflow(data.qianjiTxns, startDate, snapshotDate) : null;
  const activity = (data && startDate && snapshotDate) ? computeActivity(data.fidelityTxns, startDate, snapshotDate) : null;
  const crossCheck = (data && startDate && snapshotDate) ? computeCrossCheck(data.fidelityTxns, data.qianjiTxns, startDate, snapshotDate) : null;
  const monthlyFlows = computeMonthlyFlows(data?.qianjiTxns ?? [], startDate, snapshotDate);

  return {
    chartDaily,
    qianjiTxns: data?.qianjiTxns ?? [],
    fidelityTxns: data?.fidelityTxns ?? [],
    defaultStartIndex,
    defaultEndIndex,
    brushStart: fullRange.start,
    brushEnd: fullRange.end,
    snapshot,
    startDate,
    onBrushChange,
    loading,
    error,
    allocation,
    cashflow,
    activity,
    market: data?.market ?? null,
    holdingsDetail: data?.holdingsDetail ?? null,
    crossCheck,
    monthlyFlows,
    syncMeta: data?.syncMeta ?? null,
  };
}
