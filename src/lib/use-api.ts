"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

// ── Shared debounce timer ────────────────────────────────────────────────
// All brush-driven hooks share one timer so 3 URL changes from a single
// brush movement collapse into one 150 ms wait instead of three.

const BRUSH_DEBOUNCE = 150;

let sharedTimer: ReturnType<typeof setTimeout> | undefined;
const pendingCallbacks: Set<() => void> = new Set();

function scheduleBrushFetch(cb: () => void): void {
  pendingCallbacks.add(cb);
  clearTimeout(sharedTimer);
  sharedTimer = setTimeout(() => {
    const batch = [...pendingCallbacks];
    pendingCallbacks.clear();
    for (const fn of batch) fn();
  }, BRUSH_DEBOUNCE);
}

function cancelBrushFetch(cb: () => void): void {
  pendingCallbacks.delete(cb);
}

// ── Generic fetcher ──────────────────────────────────────────────────────

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
}

function useApi<T>(
  url: string | null,
  schema: z.ZodType<T>,
  debounced = false,
): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: !!url });

  // Stable fetch function that captures the current url via ref
  const urlRef = useRef(url);
  urlRef.current = url;

  const doFetch = useCallback(() => {
    const target = urlRef.current;
    if (!target) return;
    setState((s) => ({ ...s, loading: true }));
    fetch(target, { cache: "no-store" })
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then((json) => {
        const parsed = schema.safeParse(json);
        if (!parsed.success) {
          console.error(`API validation failed for ${target}:`, parsed.error.issues);
          setState({ data: null, loading: false });
          return;
        }
        if (urlRef.current === target) setState({ data: parsed.data, loading: false });
      })
      .catch((e) => {
        console.error(`API fetch failed for ${target}:`, e);
        if (urlRef.current === target) setState({ data: null, loading: false });
      });
  }, [schema]);

  useEffect(() => {
    if (!url) {
      setState({ data: null, loading: false });
      return;
    }

    if (debounced) {
      scheduleBrushFetch(doFetch);
      return () => cancelBrushFetch(doFetch);
    }

    doFetch();
  }, [url, debounced, doFetch]);

  return state;
}

// ── Allocation ───────────────────────────────────────────────────────────

export function useAllocation(date: string | null): ApiState<AllocationResponse> {
  const url = date ? `${API_BASE}/allocation?date=${date}` : null;
  return useApi(url, AllocationResponseSchema, true);
}

// ── Cash Flow ────────────────────────────────────────────────────────────

export function useCashflow(start: string | null, end: string | null): ApiState<CashflowResponse> {
  const url = start && end ? `${API_BASE}/cashflow?start=${start}&end=${end}` : null;
  return useApi(url, CashflowResponseSchema, true);
}

// ── Activity ─────────────────────────────────────────────────────────────

export function useActivity(start: string | null, end: string | null): ApiState<ActivityResponse> {
  const url = start && end ? `${API_BASE}/activity?start=${start}&end=${end}` : null;
  return useApi(url, ActivityResponseSchema, true);
}

// ── Market ───────────────────────────────────────────────────────────────

export function useMarket(): ApiState<MarketData> {
  const url = `${API_BASE}/market`;
  return useApi(url, MarketDataSchema);
}
