"use client";

// ── Group position-value chart (total group $ over time + B/S markers) ───

import {
  ComposedChart,
  Line,
  Scatter,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { useIsDark } from "@/lib/hooks/hooks";
import { gridStroke, axisProps } from "@/lib/format/chart-styles";
import { fmtCurrency, fmtCurrencyShort, fmtDateMedium, fmtTick } from "@/lib/format/format";
import { BuyClusterMarker, SellClusterMarker } from "./ticker-markers";
import type { GroupValuePoint, GroupNetEntry } from "@/lib/format/group-aggregation";
import { TooltipCard } from "@/components/charts/tooltip-card";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";

type GroupMarkerCluster = {
  ts: number;
  count: number;
  r: number;
  amount: number;
  price: number;
  qty: number;
  memberDates: string[];
  breakdown: { symbol: string; signed: number }[];
};

export type GroupChartPoint = GroupValuePoint & {
  buyCluster?: GroupMarkerCluster;
  sellCluster?: GroupMarkerCluster;
  buyClusterPrice?: number;
  sellClusterPrice?: number;
};

/** Combine the daily value series with the group-net markers into chart points. */
export function buildGroupChartData(
  series: GroupValuePoint[],
  markers: Map<string, GroupNetEntry>,
): GroupChartPoint[] {
  return series.map((p) => {
    const entry = markers.get(p.date);
    if (!entry) return p;
    const cluster: GroupMarkerCluster = {
      ts: p.ts,
      count: 1,
      r: 12,
      amount: entry.net,
      price: 0,
      qty: 0,
      memberDates: [p.date],
      breakdown: entry.breakdown,
    };
    if (entry.side === "buy") return { ...p, buyCluster: cluster, buyClusterPrice: p.value };
    return { ...p, sellCluster: cluster, sellClusterPrice: p.value };
  });
}

function GroupTooltip({ active, payload }: TooltipContentProps) {
  const d = payload?.[0]?.payload as GroupChartPoint | undefined;
  if (!active || !d) return null;

  const marker = d.sellCluster ?? d.buyCluster;
  // Sign convention:
  //   signed > 0  ⇒ sell contribution (exposure went down) — display negative
  //   signed < 0  ⇒ buy contribution (exposure went up)   — display positive
  const signIcon = (signed: number) => (signed >= 0 ? "−" : "+");

  return (
    <TooltipCard active={active} payload={payload} title={fmtDateMedium(d.date)}>
      <p style={{ margin: 0 }}>Value: {fmtCurrency(d.value)}</p>
      <p style={{ margin: 0 }}>Cost: {fmtCurrency(d.costBasis)}</p>
      {marker && (
        <>
          <p style={{ margin: "6px 0 0 0", fontWeight: 600, color: d.sellCluster ? SELL_COLOR : BUY_COLOR }}>
            Net {signIcon(d.sellCluster ? 1 : -1)}{fmtCurrency(marker.amount)} {d.sellCluster ? "sell" : "buy"}
          </p>
          {marker.breakdown.map((b) => (
            <p key={b.symbol} style={{ margin: 0, fontSize: 12, fontFamily: "monospace" }}>
              {b.symbol}  {signIcon(b.signed)}{fmtCurrency(Math.abs(b.signed))}
            </p>
          ))}
        </>
      )}
    </TooltipCard>
  );
}

export function GroupChart({ data }: { data: GroupChartPoint[] }) {
  const isDark = useIsDark();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={fmtTick}
          hide
          {...axisProps(isDark)}
        />
        <YAxis
          domain={["auto", "auto"]}
          tickFormatter={fmtCurrencyShort}
          width={60}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip content={GroupTooltip} />
        <Line
          type="monotone"
          dataKey="costBasis"
          stroke={isDark ? "rgba(255,255,255,0.35)" : "rgba(0,0,0,0.3)"}
          strokeWidth={1.25}
          strokeDasharray="4 4"
          dot={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={isDark ? "#60a5fa" : "#2563eb"}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
        {/* Sell first, Buy second — Buy paints on top (matches ticker-dialog ordering) */}
        <Scatter dataKey="sellClusterPrice" shape={SellClusterMarker} legendType="none" isAnimationActive={false} />
        <Scatter dataKey="buyClusterPrice" shape={BuyClusterMarker} legendType="none" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
