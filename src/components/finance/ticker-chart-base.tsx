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
import { fmtCurrency, fmtDateMedium, fmtQty, fmtTick } from "@/lib/format/format";
import { useIsDark } from "@/lib/hooks/hooks";
import { gridStroke, axisProps } from "@/lib/format/chart-styles";
import { buildClusteredData, tsToIsoLocal, type ClusteredPoint } from "@/lib/format/ticker-data";
import type { TickerChartPoint } from "@/lib/format/ticker-data";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";
import { TooltipCard } from "@/components/charts/tooltip-card";
import { BuyClusterMarker, SellClusterMarker, ReinvestMarker } from "./ticker-markers";

function PriceTooltip({ active, payload }: TooltipContentProps) {
  const d = payload?.[0]?.payload as ClusteredPoint | undefined;
  if (!d) return null;
  const clusterLine = (c: NonNullable<ClusteredPoint["buyCluster"]>, label: string, color: string) => {
    const tag = c.count > 1 ? ` ×${c.count}` : "";
    return (
      <p style={{ color, margin: 0 }}>
        {label}{tag}: {fmtQty(c.qty)} @ {fmtCurrency(c.price)} = {fmtCurrency(c.amount)}
        {c.count > 1 && <> <span style={{ opacity: 0.7 }}>(around {fmtDateMedium(tsToIsoLocal(c.ts))})</span></>}
      </p>
    );
  };
  return (
    <TooltipCard active={active} payload={payload} title={fmtDateMedium(d.date)}>
      <p style={{ margin: 0 }}>Close: {fmtCurrency(d.close)}</p>
      {d.buyCluster && clusterLine(d.buyCluster, "Buy", BUY_COLOR)}
      {d.sellCluster && clusterLine(d.sellCluster, "Sell", SELL_COLOR)}
    </TooltipCard>
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
  const clustered = buildClusteredData(data);

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={clustered} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
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
        <Scatter dataKey="reinvestDot" shape={ReinvestMarker} legendType="none" isAnimationActive={false} />
        <Scatter dataKey="sellClusterPrice" shape={SellClusterMarker} legendType="none" isAnimationActive={false} />
        <Scatter dataKey="buyClusterPrice" shape={BuyClusterMarker} legendType="none" isAnimationActive={false} />
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
