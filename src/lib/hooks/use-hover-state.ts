import { useState } from "react";
import type { Cluster } from "@/lib/format/ticker-data";

/** Snapshot of what the user's pointer is over inside a chart's clustered
 *  buy/sell marker. Owned here (with the state hook) so consumers don't
 *  reach across component layers for the type. */
export type HoverState = {
  cluster: Cluster;
  side: "buy" | "sell";
  dayIso: string;
  close: number;
  x: number;
  y: number;
};

export function useHoverState() {
  const [hover, setHover] = useState<HoverState | null>(null);
  return {
    hover,
    onEnter: (h: HoverState) => setHover(h),
    onMove: (x: number, y: number) => setHover((prev) => (prev ? { ...prev, x, y } : null)),
    onLeave: () => setHover(null),
  };
}
