export function tooltipStyle(isDark: boolean) {
  return {
    backgroundColor: isDark ? "rgba(20, 25, 45, 0.65)" : "rgba(255, 255, 255, 0.60)",
    backdropFilter: "blur(40px) saturate(200%)",
    WebkitBackdropFilter: "blur(40px) saturate(200%)",
    border: `0.5px solid ${isDark ? "rgba(255,255,255,0.12)" : "rgba(255,255,255,0.5)"}`,
    borderRadius: "16px",
    padding: "10px 14px",
    boxShadow: isDark
      ? "0 12px 40px rgba(0,0,0,0.25), inset 0 0.5px 0 rgba(255,255,255,0.12)"
      : "0 12px 40px rgba(0,0,0,0.06), inset 0 0.5px 0 rgba(255,255,255,0.6)",
  };
}

export function gridStroke(isDark: boolean): string {
  return isDark ? "rgba(255,255,255,0.05)" : "rgba(0,0,0,0.05)";
}
