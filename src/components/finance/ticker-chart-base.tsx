"use client";

// ── Inline ticker chart (non-dialog) ─────────────────────────────────────
//
// Small-format price chart rendered inside the activity-table row. Markers
// are one-per-day (no clustering) and the tooltip is the simple single-day
// variant. The larger dialog chart lives in ticker-dialog.tsx.

import {
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ReferenceLine,
  ResponsiveContainer,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { fmtCurrency, fmtDateMedium, fmtTick } from "@/lib/format";
import { getIsDark, useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/chart-styles";
import type { TickerChartPoint } from "@/lib/ticker-data";
import { BuyMarker, SellMarker, BUY_COLOR, SELL_COLOR } from "./ticker-markers";

function PriceTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const isDark = getIsDark();
  const style = tooltipStyle(isDark);
  const d = payload[0]?.payload as TickerChartPoint | undefined;
  if (!d) return null;

  return (
    <div style={style}>
      <p style={{ fontWeight: 600, marginBottom: 2 }}>{fmtDateMedium(d.date)}</p>
      <p style={{ margin: 0 }}>Close: {fmtCurrency(d.close)}</p>
      {d.buyPrice != null && (
        <p style={{ color: BUY_COLOR, margin: 0 }}>
          Buy: {d.buyQty} × {fmtCurrency(d.buyPrice)} = {fmtCurrency(d.buyAmount!)}
        </p>
      )}
      {d.sellPrice != null && (
        <p style={{ color: SELL_COLOR, margin: 0 }}>
          Sell: {d.sellQty} × {fmtCurrency(d.sellPrice)} = {fmtCurrency(d.sellAmount!)}
        </p>
      )}
    </div>
  );
}

export function TickerChartBase({
  data,
  avgCost,
  height = 200,
}: {
  data: TickerChartPoint[];
  avgCost: number | null;
  height?: number;
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
          {...axisProps(isDark)}
        />
        <YAxis
          domain={["auto", "auto"]}
          tickFormatter={(v: number) => `$${v}`}
          width={55}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip content={PriceTooltip} />
        <Line
          type="monotone"
          dataKey="close"
          stroke={isDark ? "#60a5fa" : "#2563eb"}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
        <Scatter dataKey="buyPrice" shape={BuyMarker} legendType="none" isAnimationActive={false} />
        <Scatter dataKey="sellPrice" shape={SellMarker} legendType="none" isAnimationActive={false} />
        {avgCost != null && (
          <ReferenceLine
            y={avgCost}
            stroke={isDark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.2)"}
            strokeDasharray="4 4"
            label={{
              value: `Avg ${fmtCurrency(avgCost)}`,
              position: "right",
              fill: isDark ? "rgba(255,255,255,0.5)" : "rgba(0,0,0,0.4)",
              fontSize: 10,
            }}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}
