"use client";

import { useCallback, useEffect, useState } from "react";
import type { z } from "zod";
import { FETCH_TIMEOUT_MS } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";

export function useEndpointData<T>(
  url: string,
  schema: z.ZodType<T>,
  fallbackError = "Failed to load",
): {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
} {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (cancelled: () => boolean = () => false) => {
    setLoading(true);
    setError(null);
    try {
      const parsed = await fetchWithSchema(url, schema, {
        cache: "no-store",
        timeoutMs: FETCH_TIMEOUT_MS,
      });
      if (!cancelled()) setData(parsed);
    } catch (e) {
      if (!cancelled()) setError(e instanceof Error ? e.message : fallbackError);
    } finally {
      if (!cancelled()) setLoading(false);
    }
  }, [fallbackError, schema, url]);

  useEffect(() => {
    let cancelled = false;
    void load(() => cancelled);
    return () => { cancelled = true; };
  }, [load]);

  return { data, loading, error, reload: load };
}
