"use client";

// ── Shared ComposedChart skeleton (grid + axes + tooltip) ─────────────
// Used by the inline per-ticker chart, the ticker dialog chart, and the
// group chart. Each caller injects its own <Line> / <Scatter> / <ReferenceLine>
// children. Parameterized only where the three charts genuinely diverge:
// YAxis tick format and width, whether to hide the XAxis (dialog views do),
// and tooltip content (each view has its own).

import type { ReactNode, CSSProperties } from "react";
import {
  ComposedChart,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { useIsDark } from "@/lib/hooks/use-is-dark";
import { gridStroke, axisProps } from "@/lib/format/chart-styles";
import { fmtTick } from "@/lib/format/format";

export function MarkerChart({
  data,
  height = "100%",
  yTickFormatter,
  yWidth = 55,
  hideXAxis = false,
  tooltipContent,
  tooltipWrapperStyle,
  children,
}: {
  data: object[];
  height?: number | `${number}%`;
  yTickFormatter: (v: number) => string;
  yWidth?: number;
  hideXAxis?: boolean;
  tooltipContent: (props: TooltipContentProps) => ReactNode;
  tooltipWrapperStyle?: CSSProperties;
  children: ReactNode;
}) {
  const isDark = useIsDark();
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={fmtTick}
          hide={hideXAxis}
          {...axisProps(isDark)}
        />
        <YAxis
          domain={["auto", "auto"]}
          tickFormatter={yTickFormatter}
          width={yWidth}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip content={tooltipContent} wrapperStyle={tooltipWrapperStyle} />
        {children}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
