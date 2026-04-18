// ── Shared liquid-glass tooltip shell for Recharts ──────────────────────
//
// Every custom Tooltip in this repo starts with the same three lines:
//   if (!active || !payload?.length) return null;
//   const isDark = getIsDark();
//   <div style={tooltipStyle(isDark)}> + optional bold title
//
// TooltipCard collapses that into a single component. A render prop exposes
// `isDark` to the body so callers can theme row colors without re-calling
// the hook themselves.

import type { ReactNode } from "react";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { getIsDark } from "@/lib/hooks";
import { tooltipStyle } from "@/lib/chart-styles";

type Props = Pick<TooltipContentProps, "active" | "payload"> & {
  title?: ReactNode;
  children: ReactNode | ((isDark: boolean) => ReactNode);
};

export function TooltipCard({ active, payload, title, children }: Props) {
  if (!active || !payload?.length) return null;
  const isDark = getIsDark();
  return (
    <div style={tooltipStyle(isDark)}>
      {title != null && <p style={{ fontWeight: 600, marginBottom: 2 }}>{title}</p>}
      {typeof children === "function" ? children(isDark) : children}
    </div>
  );
}
