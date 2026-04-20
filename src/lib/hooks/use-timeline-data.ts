"use client";

import { useEffect, useState } from "react";
import { FETCH_TIMEOUT_MS, TIMELINE_URL } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";
import { TimelineDataSchema, type TimelineData } from "@/lib/schemas";

/** Fetch /timeline once on mount. The Zod safeParse inside
 *  `fetchWithSchema` is the single drift checkpoint between the
 *  Worker payload and the frontend's expected shape. */
export function useTimelineData(): {
  data: TimelineData | null;
  loading: boolean;
  error: string | null;
} {
  const [data, setData] = useState<TimelineData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

  return { data, loading, error };
}
