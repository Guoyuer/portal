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

// ── Hook ────────────────────────────────────────────────────────────────

export interface TimelineState {
  /** Downsampled data for the chart (~150 points, smooth brush) */
  chartDaily: DailyPoint[];
  /** Default brush indices (initial only, not controlled) */
  defaultStartIndex: number;
  defaultEndIndex: number;
  /** Point-in-time snapshot at right edge (full daily precision) */
  snapshot: DailyPoint | null;
  /** Range aggregation over brush selection (O(1) prefix sum) */
  range: PrefixPoint | null;
  /** Start date of the brush selection */
  startDate: string | null;
  /** Brush change handler — updates summary without re-rendering chart */
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
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
  // Always include the last point
  if (toFull[toFull.length - 1] !== daily.length - 1) {
    sampled.push(daily[daily.length - 1]);
    toFull.push(daily.length - 1);
  }
  return { sampled, toFull };
}

export function useTimeline(): TimelineState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [defaultStartIndex, setDefaultStartIndex] = useState(0);
  const [defaultEndIndex, setDefaultEndIndex] = useState(0);

  // Brush indices stored in ref to avoid re-rendering the chart during drag.
  // A separate state counter triggers summary re-computation via useMemo.
  const brushRef = useRef({ start: 0, end: 0 });
  const [brushTick, setBrushTick] = useState(0);
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
        if (!cancelled) {
          setData(parsed.data);
          const { sampled } = downsample(parsed.data.daily);
          const end = sampled.length - 1;
          const start = Math.max(0, end - Math.floor(252 / Math.max(1, Math.floor(parsed.data.daily.length / TARGET_CHART_POINTS))));
          setDefaultStartIndex(start);
          setDefaultEndIndex(end);
          brushRef.current = { start, end };
          setBrushTick(1);
        }
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

  // Update ref immediately (no re-render), schedule state update via rAF
  const onBrushChange = useCallback((state: { startIndex?: number; endIndex?: number }) => {
    if (state.startIndex !== undefined) brushRef.current.start = state.startIndex;
    if (state.endIndex !== undefined) brushRef.current.end = state.endIndex;
    cancelAnimationFrame(rafRef.current);
    rafRef.current = requestAnimationFrame(() => {
      setBrushTick((t) => t + 1);
    });
  }, []);

  // Map brush indices back to full daily array for precise lookups
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const _tick = brushTick; // subscribe to updates
  const fullStart = toFull[brushRef.current.start] ?? 0;
  const fullEnd = toFull[brushRef.current.end] ?? 0;

  const snapshot = useMemo(() => data?.daily[fullEnd] ?? null, [data, fullEnd]);
  const range = useMemo(() => data ? prefixRange(data.prefix, fullStart, fullEnd) : null, [data, fullStart, fullEnd]);
  const startDate = data?.daily[fullStart]?.date ?? null;

  return { chartDaily, defaultStartIndex, defaultEndIndex, snapshot, range, startDate, onBrushChange, loading, error };
}
