"use client";

import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from "recharts";
import type { MonthlyFlowPoint } from "@/lib/schema";
import { useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/chart-styles";
import { fmtMonth } from "@/lib/format";

const SR_GOOD = 30;

export function SavingsTrend({ data }: { data: MonthlyFlowPoint[] }) {
  const isDark = useIsDark();

  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={200}>
      <LineChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} vertical={false} />
        <XAxis dataKey="month" tickFormatter={fmtMonth} {...axisProps(isDark)} />
        <YAxis
          tickFormatter={(v) => `${v}%`}
          width={40}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip
          contentStyle={tooltipStyle(isDark)}
          formatter={(value) => [`${Math.round(Number(value))}%`, "Savings Rate"]}
          labelFormatter={(m) => fmtMonth(String(m))}
        />
        <ReferenceLine
          y={SR_GOOD}
          stroke={isDark ? "rgba(255,255,255,0.15)" : "rgba(0,0,0,0.12)"}
          strokeDasharray="4 4"
          label={{
            value: `${SR_GOOD}%`,
            position: "right",
            fill: isDark ? "rgba(255,255,255,0.3)" : "rgba(0,0,0,0.25)",
            fontSize: 10,
          }}
        />
        <Line
          type="monotone"
          dataKey="savingsRate"
          stroke="#059669"
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
