"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
  daily: DailyPoint[];
  prefix: PrefixPoint[];
  startIndex: number;
  endIndex: number;
  snapshot: DailyPoint | null;      // point-in-time at right edge
  range: PrefixPoint | null;        // aggregated over brush selection
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
}

export function useTimeline(): TimelineState {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [startIndex, setStartIndex] = useState(0);
  const [endIndex, setEndIndex] = useState(0);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(TIMELINE_URL, { cache: "no-store" });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const json = await res.json();
        const parsed = TimelineDataSchema.safeParse(json);
        if (!parsed.success) throw new Error("Invalid timeline data");
        if (!cancelled) {
          setData(parsed.data);
          const end = parsed.data.daily.length - 1;
          const start = Math.max(0, end - 252);
          setStartIndex(start);
          setEndIndex(end);
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load timeline");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const onBrushChange = useCallback((state: { startIndex?: number; endIndex?: number }) => {
    if (state.startIndex !== undefined) setStartIndex(state.startIndex);
    if (state.endIndex !== undefined) setEndIndex(state.endIndex);
  }, []);

  const snapshot = useMemo(() => data?.daily[endIndex] ?? null, [data, endIndex]);
  const range = useMemo(() => data ? prefixRange(data.prefix, startIndex, endIndex) : null, [data, startIndex, endIndex]);

  return { daily: data?.daily ?? [], prefix: data?.prefix ?? [], startIndex, endIndex, snapshot, range, onBrushChange, loading, error };
}
