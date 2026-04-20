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

// Sqrt-normalized radius — magnitude is immediately perceptible. Smaller
// cap than ticker-chart (22) because group charts often show more markers
// clustered together (every rebalance/DCA) and the value line must remain
// visible between them.
const MIN_R = 7;
const MAX_R = 14;

/** Combine the daily value series with the group-net markers into chart points. */
export function buildGroupChartData(
  series: GroupValuePoint[],
  markers: Map<string, GroupNetEntry>,
): GroupChartPoint[] {
  const maxAmount = Math.max(1, ...Array.from(markers.values(), (m) => m.net));
  return series.map((p) => {
    const entry = markers.get(p.date);
    if (!entry) return p;
    const cluster: GroupMarkerCluster = {
      ts: p.ts,
      count: 1,
      r: MIN_R + (MAX_R - MIN_R) * Math.sqrt(entry.net / maxAmount),
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
          {/* Breakdown is only informative when multiple tickers contribute —
              for single-ticker groups the breakdown line would just restate the net. */}
          {marker.breakdown.length > 1 && marker.breakdown.map((b) => (
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
  const latestCost = data[data.length - 1]?.costBasis;
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
          label={(props: { index?: number; x?: number | string; y?: number | string }) => {
            const xN = typeof props.x === "number" ? props.x : Number(props.x);
            const yN = typeof props.y === "number" ? props.y : Number(props.y);
            if (props.index !== data.length - 1 || latestCost == null || !Number.isFinite(xN) || !Number.isFinite(yN)) return <g />;
            return (
              <text
                x={xN - 4}
                y={yN - 6}
                textAnchor="end"
                fill={isDark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)"}
                fontSize={10}
              >
                Cost {fmtCurrency(latestCost)}
              </text>
            );
          }}
        />
        <Line
          type="monotone"
          dataKey="value"
          stroke={isDark ? "#60a5fa" : "#2563eb"}
          strokeWidth={2}
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
