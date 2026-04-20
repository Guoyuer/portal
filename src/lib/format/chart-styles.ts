// ── Chart style tokens ─────────────────────────────────────────────────
// Two frozen variants per style (light/dark) hoisted to module scope so
// recharts sees a stable reference each render — no realloc per call.

const TOOLTIP_LIGHT = {
  backgroundColor: "rgba(255, 255, 255, 0.85)",
  backdropFilter: "blur(40px) saturate(200%)",
  WebkitBackdropFilter: "blur(40px) saturate(200%)",
  border: "0.5px solid rgba(255,255,255,0.5)",
  borderRadius: "16px",
  padding: "10px 14px",
  fontSize: "12px",
  lineHeight: "1.6",
  boxShadow: "0 12px 40px rgba(0,0,0,0.06), inset 0 0.5px 0 rgba(255,255,255,0.6)",
} as const;

const TOOLTIP_DARK = {
  backgroundColor: "rgba(8, 15, 30, 0.85)",
  backdropFilter: "blur(40px) saturate(200%)",
  WebkitBackdropFilter: "blur(40px) saturate(200%)",
  border: "0.5px solid rgba(34,211,238,0.12)",
  borderRadius: "16px",
  padding: "10px 14px",
  fontSize: "12px",
  lineHeight: "1.6",
  boxShadow: "0 12px 40px rgba(0,0,0,0.35), inset 0 0.5px 0 rgba(34,211,238,0.08)",
} as const;

export function tooltipStyle(isDark: boolean) {
  return isDark ? TOOLTIP_DARK : TOOLTIP_LIGHT;
}

export function gridStroke(isDark: boolean): string {
  return isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.05)";
}

const AXIS_LIGHT = {
  fontSize: 11,
  tick: { fill: "#6b7280" },
  axisLine: { stroke: "rgba(0,0,0,0.05)" },
  tickLine: false,
} as const;

const AXIS_DARK = {
  fontSize: 11,
  tick: { fill: "#9ca3af" },
  axisLine: { stroke: "rgba(255,255,255,0.05)" },
  tickLine: false,
} as const;

export function axisProps(isDark: boolean) {
  return isDark ? AXIS_DARK : AXIS_LIGHT;
}

const BRUSH_LIGHT = { stroke: "#0891b2", fill: "rgba(207,250,254,0.5)" } as const;
const BRUSH_DARK = { stroke: "#22d3ee", fill: "rgba(8,145,178,0.2)" } as const;

export function brushColors(isDark: boolean) {
  return isDark ? BRUSH_DARK : BRUSH_LIGHT;
}

/** Liquid-glass styled legend wrapper to match `tooltipStyle()`. */
const LEGEND_LIGHT = {
  paddingTop: "8px",
  background: "rgba(255,255,255,0.4)",
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  borderRadius: "10px",
  padding: "4px 12px",
  border: "1px solid rgba(255,255,255,0.3)",
} as const;

const LEGEND_DARK = {
  paddingTop: "8px",
  background: "rgba(255,255,255,0.03)",
  backdropFilter: "blur(12px)",
  WebkitBackdropFilter: "blur(12px)",
  borderRadius: "10px",
  padding: "4px 12px",
  border: "1px solid rgba(255,255,255,0.06)",
} as const;

export function legendStyle(isDark: boolean) {
  return isDark ? LEGEND_DARK : LEGEND_LIGHT;
}
