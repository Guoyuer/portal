export function tooltipStyle(isDark: boolean) {
  return {
    backgroundColor: isDark ? "rgba(8, 15, 30, 0.85)" : "rgba(255, 255, 255, 0.85)",
    backdropFilter: "blur(40px) saturate(200%)",
    WebkitBackdropFilter: "blur(40px) saturate(200%)",
    border: `0.5px solid ${isDark ? "rgba(34,211,238,0.12)" : "rgba(255,255,255,0.5)"}`,
    borderRadius: "16px",
    padding: "10px 14px",
    fontSize: "12px",
    lineHeight: "1.6",
    boxShadow: isDark
      ? "0 12px 40px rgba(0,0,0,0.35), inset 0 0.5px 0 rgba(34,211,238,0.08)"
      : "0 12px 40px rgba(0,0,0,0.06), inset 0 0.5px 0 rgba(255,255,255,0.6)",
  };
}

export function gridStroke(isDark: boolean): string {
  return isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.05)";
}

export function axisProps(isDark: boolean) {
  return {
    fontSize: 11,
    tick: { fill: isDark ? "#9ca3af" : "#6b7280" },
    axisLine: { stroke: gridStroke(isDark) },
    tickLine: false,
  } as const;
}

export function brushColors(isDark: boolean) {
  return {
    stroke: isDark ? "#22d3ee" : "#0891b2",
    fill: isDark ? "rgba(8,145,178,0.2)" : "rgba(207,250,254,0.5)",
  } as const;
}
