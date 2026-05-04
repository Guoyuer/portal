"use client";

import { useEffect, useState } from "react";
import type { TimelineData } from "@/lib/schemas/timeline";

// Default brush range shows ~1 year (252 US trading days per calendar year).
const TRADING_DAYS_PER_YEAR = 252;

/** Brush state + default window + reset-on-data-arrival effect.
 *  Kept independent of the /timeline fetch so the two concerns are
 *  individually testable and the consumer hook stays a thin orchestrator. */
export function useBrushRange(data: TimelineData | null): {
  brushStart: number;
  brushEnd: number;
  defaultStartIndex: number;
  defaultEndIndex: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
} {
  const len = data?.daily.length ?? 0;
  const defaultEndIndex = len > 0 ? len - 1 : 0;
  const defaultStartIndex = len === 0
    ? 0
    : Math.max(0, defaultEndIndex - TRADING_DAYS_PER_YEAR);

  const [range, setRange] = useState({ start: 0, end: 0 });

  useEffect(() => {
    if (data && len > 0) {
      setRange({ start: defaultStartIndex, end: defaultEndIndex });
    }
  // Reset the brush window whenever a new bundle arrives; default indices
  // depend only on data.daily.length, which is captured by the data dep.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);

  const onBrushChange = (state: { startIndex?: number; endIndex?: number }) => {
    setRange((prev) => ({
      start: state.startIndex ?? prev.start,
      end: state.endIndex ?? prev.end,
    }));
  };

  return {
    brushStart: range.start,
    brushEnd: range.end,
    defaultStartIndex,
    defaultEndIndex,
    onBrushChange,
  };
}
