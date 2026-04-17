"use client";

import { useEffect, useState } from "react";
import { TIMELINE_URL } from "@/lib/config";
import { fetchWithSchema } from "@/lib/fetch-schema";
import {
  TimelineDataSchema,
  type CategoryMeta,
  type MarketData,
  type StockDetail,
  type DailyPoint,
  type QianjiTxn,
  type FidelityTxn,
  type TimelineData,
} from "@/lib/schemas";
import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  MonthlyFlowPoint,
} from "@/lib/computed-types";
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

const FETCH_TIMEOUT_MS = 10_000;

// ── Hook ────────────────────────────────────────────────────────────────

export type { CrossCheck };

export interface BundleState {
  chartDaily: DailyPoint[];
  qianjiTxns: QianjiTxn[];
  fidelityTxns: FidelityTxn[];
  categories: CategoryMeta[];
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
  // ── Per-section errors (populated when the Worker could not load a view) ──
  marketError: string | null;
  holdingsError: string | null;
  txnsError: string | null;
}

export function useBundle(): BundleState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [fullRange, setFullRange] = useState({ start: 0, end: 0 });

  // ── Fetch once ──────────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;
    fetchWithSchema(TIMELINE_URL, TimelineDataSchema, {
      cache: "no-store",
      timeoutMs: FETCH_TIMEOUT_MS,
    })
      .then((parsed) => { if (!cancelled) setData(parsed); })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  // ── Indexes (built once when data arrives) ──────────────────────────
  const dateIndex = data ? buildDateIndex(data.daily) : new Map<string, number>();
  const tickerIndex = data ? buildTickerIndex(data.dailyTickers) : new Map();

  // ── Chart data (no downsampling — show every day) ───────────────────
  const chartDaily = data?.daily ?? [];

  const defaultEndIndex = chartDaily.length > 0 ? chartDaily.length - 1 : 0;
  // Default brush range shows ~1 year (252 US trading days per calendar year)
  const TRADING_DAYS_PER_YEAR = 252;
  const defaultStartIndex = (!data || chartDaily.length === 0)
    ? 0
    : Math.max(0, defaultEndIndex - TRADING_DAYS_PER_YEAR);

  useEffect(() => {
    if (data && chartDaily.length > 0) {
      setFullRange({ start: defaultStartIndex, end: defaultEndIndex });
    }
  }, [data, chartDaily.length, defaultStartIndex, defaultEndIndex]);

  const onBrushChange = (state: { startIndex?: number; endIndex?: number }) => {
    setFullRange((prev) => ({
      start: state.startIndex ?? prev.start,
      end: state.endIndex ?? prev.end,
    }));
  };

  // ── Derived timeline state (instant — user sees these during drag) ──
  const snapshot = data?.daily[fullRange.end] ?? null;
  const startDate = data?.daily[fullRange.start]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;

  // ── Computed data (pure, instant) ───────────────────────────────────
  const categories = data?.categories ?? [];
  const allocation = (data && snapshotDate) ? computeAllocation(data.daily, tickerIndex, dateIndex, snapshotDate, categories) : null;
  const cashflow = (data && startDate && snapshotDate) ? computeCashflow(data.qianjiTxns, startDate, snapshotDate) : null;
  const activity = (data && startDate && snapshotDate) ? computeActivity(data.fidelityTxns, startDate, snapshotDate) : null;
  const crossCheck = (data && startDate && snapshotDate) ? computeCrossCheck(data.fidelityTxns, data.qianjiTxns, startDate, snapshotDate) : null;
  const monthlyFlows = computeMonthlyFlows(data?.qianjiTxns ?? [], startDate, snapshotDate);

  return {
    chartDaily,
    qianjiTxns: data?.qianjiTxns ?? [],
    fidelityTxns: data?.fidelityTxns ?? [],
    categories,
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
    marketError: data?.errors?.market ?? null,
    holdingsError: data?.errors?.holdings ?? null,
    txnsError: data?.errors?.txns ?? null,
  };
}
