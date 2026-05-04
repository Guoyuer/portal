"use client";

import { computeBundle, type ComputedBundle } from "@/lib/compute/compute-bundle";
import { useTimelineData } from "./use-timeline-data";
import { useBrushRange } from "./use-brush-range";

export type BundleState = ComputedBundle & ReturnType<typeof useBrushRange> & {
  loading: boolean;
  error: string | null;
};

/** Finance dashboard's single data entry point. Orchestrates three layers:
 *  fetch+parse (`useTimelineData`), brush window state (`useBrushRange`),
 *  and the pure compute pipeline (`computeBundle`). */
export function useBundle(): BundleState {
  const { data, loading, error } = useTimelineData();
  const brush = useBrushRange(data);
  const computed = computeBundle(data, brush.brushStart, brush.brushEnd);
  return { ...computed, ...brush, loading, error };
}
