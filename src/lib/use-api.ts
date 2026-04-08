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
  debounceMs = 0,
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: !!url });
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (!url) {
      setState({ data: null, loading: false });
      return;
    }

    let cancelled = false;

    const doFetch = () => {
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
    };

    if (debounceMs > 0) {
      clearTimeout(timerRef.current);
      timerRef.current = setTimeout(doFetch, debounceMs);
    } else {
      doFetch();
    }

    return () => {
      cancelled = true;
      clearTimeout(timerRef.current);
    };
  }, [url, schema, debounceMs]);

  return state;
}

// ── Allocation ───────────────────────────────────────────────────────────

const BRUSH_DEBOUNCE = 150; // ms — skip intermediate brush positions during drag

export function useAllocation(date: string | null): ApiState<AllocationResponse> {
  const url = date ? `${API_BASE}/allocation?date=${date}` : null;
  return useApi(url, AllocationResponseSchema, BRUSH_DEBOUNCE);
}

// ── Cash Flow ────────────────────────────────────────────────────────────

export function useCashflow(start: string | null, end: string | null): ApiState<CashflowResponse> {
  const url = start && end ? `${API_BASE}/cashflow?start=${start}&end=${end}` : null;
  return useApi(url, CashflowResponseSchema, BRUSH_DEBOUNCE);
}

// ── Activity ─────────────────────────────────────────────────────────────

export function useActivity(start: string | null, end: string | null): ApiState<ActivityResponse> {
  const url = start && end ? `${API_BASE}/activity?start=${start}&end=${end}` : null;
  return useApi(url, ActivityResponseSchema, BRUSH_DEBOUNCE);
}

// ── Market ───────────────────────────────────────────────────────────────

export function useMarket(): ApiState<MarketData> {
  const url = `${API_BASE}/market`;
  return useApi(url, MarketDataSchema);
}
