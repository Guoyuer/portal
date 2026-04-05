export function tooltipStyle(isDark: boolean) {
  return {
    backgroundColor: isDark ? "#1e293b" : "#fff",
    border: `1px solid ${isDark ? "#334155" : "#e5e7eb"}`,
    borderRadius: "8px",
    padding: "8px 12px",
  };
}

export function gridStroke(isDark: boolean): string {
  return isDark ? "#334155" : "#e5e7eb";
}
