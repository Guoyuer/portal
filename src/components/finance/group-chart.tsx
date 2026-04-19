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
import { useIsDark } from "@/lib/hooks/hooks";
import { gridStroke, axisProps } from "@/lib/format/chart-styles";
import { fmtCurrencyShort, fmtTick } from "@/lib/format/format";
import { BuyClusterMarker, SellClusterMarker } from "./ticker-markers";
import type { GroupValuePoint, GroupNetEntry } from "@/lib/format/group-aggregation";

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
        <Tooltip />
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
