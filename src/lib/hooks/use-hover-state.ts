import { useState } from "react";
import type { HoverState } from "@/components/finance/ticker-markers";

export function useHoverState() {
  const [hover, setHover] = useState<HoverState | null>(null);
  return {
    hover,
    onEnter: (h: HoverState) => setHover(h),
    onMove: (x: number, y: number) => setHover((prev) => (prev ? { ...prev, x, y } : null)),
    onLeave: () => setHover(null),
  };
}
