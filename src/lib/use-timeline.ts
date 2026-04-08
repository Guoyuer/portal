"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { TIMELINE_URL } from "@/lib/config";
import { TimelineDataSchema, type DailyPoint, type PrefixPoint, type TimelineData } from "@/lib/schema";

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

// ── Hook ────────────────────────────────────────────────────────────────

export interface TimelineState {
  chartDaily: DailyPoint[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  snapshot: DailyPoint | null;
  range: PrefixPoint | null;
  startDate: string | null;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
}

export function useTimeline(): TimelineState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Full-resolution indices derived from brush position.
  // Updated via rAF so the chart (memo'd) never re-renders during drag.
  const [fullRange, setFullRange] = useState({ start: 0, end: 0 });
  const brushRef = useRef({ start: 0, end: 0 }); // accumulates partial onChange calls
  const rafRef = useRef(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(TIMELINE_URL, { cache: "no-store", signal: AbortSignal.timeout(3000) });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const json = await res.json();
        const parsed = TimelineDataSchema.safeParse(json);
        if (!parsed.success) throw new Error("Invalid timeline data");
        if (!cancelled) setData(parsed.data);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load timeline");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const { sampled: chartDaily, toFull } = useMemo(
    () => data ? downsample(data.daily) : { sampled: [] as DailyPoint[], toFull: [] as number[] },
    [data],
  );

  // Default brush window: last ~252 trading days (1 year)
  const defaultEndIndex = chartDaily.length > 0 ? chartDaily.length - 1 : 0;
  const defaultStartIndex = useMemo(() => {
    if (!data || chartDaily.length === 0) return 0;
    const step = Math.max(1, Math.floor(data.daily.length / TARGET_CHART_POINTS));
    return Math.max(0, defaultEndIndex - Math.floor(252 / step));
  }, [data, chartDaily.length, defaultEndIndex]);

  // Seed fullRange once when data arrives
  useEffect(() => {
    if (data && toFull.length > 0) {
      const s = toFull[defaultStartIndex] ?? 0;
      const e = toFull[defaultEndIndex] ?? 0;
      brushRef.current = { start: defaultStartIndex, end: defaultEndIndex };
      setFullRange({ start: s, end: e });
    }
  }, [data, toFull, defaultStartIndex, defaultEndIndex]);

  // Accumulate partial onChange, debounce via rAF, map to full indices
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

  const snapshot = useMemo(() => data?.daily[fullRange.end] ?? null, [data, fullRange.end]);
  const range = useMemo(() => data ? prefixRange(data.prefix, fullRange.start, fullRange.end) : null, [data, fullRange.start, fullRange.end]);
  const startDate = data?.daily[fullRange.start]?.date ?? null;

  return { chartDaily, defaultStartIndex, defaultEndIndex, snapshot, range, startDate, onBrushChange, loading, error };
}
