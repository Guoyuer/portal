"use client";

import { useEffect, useRef, useState } from "react";
import { API_BASE } from "@/lib/config";
import {
  AllocationResponseSchema,
  CashflowResponseSchema,
  ActivityResponseSchema,
  MarketDataSchema,
  type AllocationResponse,
  type CashflowResponse,
  type ActivityResponse,
  type MarketData,
} from "@/lib/schema";
import type { z } from "zod";

// ── Generic fetcher ──────────────────────────────────────────────────────

interface ApiState<T> {
  data: T | null;
  loading: boolean;
}

function useApi<T>(
  url: string | null,
  schema: z.ZodType<T>,
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: !!url });
  const prevUrl = useRef<string | null>(null);

  useEffect(() => {
    if (!url) {
      setState({ data: null, loading: false });
      prevUrl.current = null;
      return;
    }

    // Skip if URL hasn't changed
    if (url === prevUrl.current) return;
    prevUrl.current = url;

    let cancelled = false;
    setState((s) => ({ ...s, loading: true }));

    (async () => {
      try {
        const res = await fetch(url, { cache: "no-store" });
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const json = await res.json();
        const parsed = schema.safeParse(json);
        if (!parsed.success) {
          console.error(`API validation failed for ${url}:`, parsed.error.issues);
          if (!cancelled) setState({ data: null, loading: false });
          return;
        }
        if (!cancelled) setState({ data: parsed.data, loading: false });
      } catch (e) {
        console.error(`API fetch failed for ${url}:`, e);
        if (!cancelled) setState({ data: null, loading: false });
      }
    })();

    return () => { cancelled = true; };
  }, [url, schema]);

  return state;
}

// ── Allocation ───────────────────────────────────────────────────────────

export function useAllocation(date: string | null): ApiState<AllocationResponse> {
  const url = date ? `${API_BASE}/allocation?date=${date}` : null;
  return useApi(url, AllocationResponseSchema);
}

// ── Cash Flow ────────────────────────────────────────────────────────────

export function useCashflow(start: string | null, end: string | null): ApiState<CashflowResponse> {
  const url = start && end ? `${API_BASE}/cashflow?start=${start}&end=${end}` : null;
  return useApi(url, CashflowResponseSchema);
}

// ── Activity ─────────────────────────────────────────────────────────────

export function useActivity(start: string | null, end: string | null): ApiState<ActivityResponse> {
  const url = start && end ? `${API_BASE}/activity?start=${start}&end=${end}` : null;
  return useApi(url, ActivityResponseSchema);
}

// ── Market ───────────────────────────────────────────────────────────────

export function useMarket(): ApiState<MarketData> {
  const url = `${API_BASE}/market`;
  return useApi(url, MarketDataSchema);
}
