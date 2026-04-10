"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  downsample,
  buildDateIndex,
  buildTickerIndex,
  TARGET_CHART_POINTS,
  type CrossCheck,
} from "@/lib/compute";

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
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  market: MarketData | null;
  holdingsDetail: StockDetail[] | null;
  crossCheck: CrossCheck | null;
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
    setFullRange({
      start: toFull[brushRef.current.start] ?? 0,
      end: toFull[brushRef.current.end] ?? 0,
    });
  }, [toFull]);

  // ── Derived timeline state (instant — user sees these during drag) ──
  const snapshot = useMemo(() => data?.daily[fullRange.end] ?? null, [data, fullRange.end]);
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

  const crossCheck = useMemo(
    () => (data && startDate && snapshotDate) ? computeCrossCheck(data.fidelityTxns, data.qianjiTxns, startDate, snapshotDate) : null,
    [data, startDate, snapshotDate],
  );

  return {
    chartDaily,
    qianjiTxns: data?.qianjiTxns ?? [],
    fidelityTxns: data?.fidelityTxns ?? [],
    defaultStartIndex,
    defaultEndIndex,
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
    syncMeta: data?.syncMeta ?? null,
  };
}
