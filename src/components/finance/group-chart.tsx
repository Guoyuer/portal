"use client";

// ── Group proxy-price chart (representative ticker price over time + B/S markers) ──

import { Line, Scatter } from "recharts";
import type { CSSProperties } from "react";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { useIsDark } from "@/lib/hooks/hooks";
import { fmtCurrency, fmtCurrencyShort, fmtDateMedium, parseLocalDate } from "@/lib/format/format";
import { BuyClusterMarker, SellClusterMarker, type ClusterMarkerProps, type HoverState, type Selection } from "./ticker-markers";
import { MarkerChart } from "./marker-chart";
import type { GroupNetEntry } from "@/lib/format/group-aggregation";
import type { Cluster } from "@/lib/format/ticker-data";
import { scaleR } from "@/lib/format/ticker-data";
import { TooltipCard } from "@/components/charts/tooltip-card";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";

// ── Chart point shape ────────────────────────────────────────────────────

export type GroupChartPoint = {
  date: string;
  ts: number;
  price: number;             // proxy ticker's close price that day
  buyCluster?: Cluster;
  sellCluster?: Cluster;
  buyClusterPrice?: number;  // = price (for recharts Scatter dataKey)
  sellClusterPrice?: number; // = price
};

// Smaller radius cap than ticker-chart (22) — group charts often show more
// markers clustered together (every rebalance/DCA) and the price line must
// remain visible between them.
const MIN_R = 7;
const MAX_R = 14;

/**
 * Combine the representative ticker's price series with the group-net
 * markers into chart points.
 *
 * proxyPrices: Map<ISO date, close price>
 * markers: Map<ISO date, GroupNetEntry>  (from groupNetByDate)
 */
export function buildGroupChartData(
  proxyPrices: Map<string, number>,
  markers: Map<string, GroupNetEntry>,
): GroupChartPoint[] {
  const maxAmount = Math.max(1, ...Array.from(markers.values(), (m) => m.net));

  const points: GroupChartPoint[] = [];
  for (const [date, close] of proxyPrices) {
    const ts = parseLocalDate(date).getTime();
    const entry = markers.get(date);
    if (!entry) {
      points.push({ date, ts, price: close });
      continue;
    }
    const cluster: Cluster = {
      ts,
      count: 1,
      r: scaleR(entry.net, maxAmount, MIN_R, MAX_R),
      amount: entry.net,
      price: 0,
      qty: 0,
      memberDates: [date],
      breakdown: entry.breakdown,
    };
    if (entry.side === "buy") {
      points.push({ date, ts, price: close, buyCluster: cluster, buyClusterPrice: close });
    } else {
      points.push({ date, ts, price: close, sellCluster: cluster, sellClusterPrice: close });
    }
  }
  // Ensure chronological order (Map iteration order is insertion order, but
  // callers may build the Map from unsorted data)
  points.sort((a, b) => a.ts - b.ts);
  return points;
}

// ── Tooltip ───────────────────────────────────────────────────────────────

function GroupTooltip({ active, payload, representative }: TooltipContentProps & { representative: string }) {
  const d = payload?.[0]?.payload as GroupChartPoint | undefined;
  if (!active || !d) return null;

  const marker = d.sellCluster ?? d.buyCluster;
  // Sign convention:
  //   signed > 0  ⇒ sell contribution (exposure went down) — display negative
  //   signed < 0  ⇒ buy contribution (exposure went up)   — display positive
  const signIcon = (signed: number) => (signed >= 0 ? "−" : "+");

  return (
    <TooltipCard active={active} payload={payload} title={fmtDateMedium(d.date)}>
      <p style={{ margin: 0 }}>{representative}: {fmtCurrency(d.price)}</p>
      {marker && (
        <>
          <p style={{ margin: "6px 0 0 0", fontWeight: 600, color: d.sellCluster ? SELL_COLOR : BUY_COLOR }}>
            Net {signIcon(d.sellCluster ? 1 : -1)}{fmtCurrency(marker.amount)} {d.sellCluster ? "sell" : "buy"}
          </p>
          <p style={{ margin: "2px 0 0 0", fontSize: 11, opacity: 0.6 }}>
            (marker at {representative} price on this date)
          </p>
          {/* Breakdown is only informative when multiple tickers contribute —
              for single-ticker groups the breakdown line would just restate the net. */}
          {marker.breakdown && marker.breakdown.length > 1 && marker.breakdown.map((b) => (
            <p key={b.symbol} style={{ margin: 0, fontSize: 12, fontFamily: "monospace" }}>
              {b.symbol}{"  "}{signIcon(b.signed)}{fmtCurrency(Math.abs(b.signed))}
            </p>
          ))}
        </>
      )}
    </TooltipCard>
  );
}

// ── Chart component ───────────────────────────────────────────────────────

export type GroupChartInteractiveProps = {
  onEnter?: (h: HoverState) => void;
  onMove?: (x: number, y: number) => void;
  onLeave?: () => void;
  onSelect?: (sel: Selection | null) => void;
  selectedKey?: string | null;
  tooltipWrapperStyle?: CSSProperties;
};

export function GroupChart({
  data,
  representative,
  onEnter,
  onMove,
  onLeave,
  onSelect,
  selectedKey,
  tooltipWrapperStyle,
}: { data: GroupChartPoint[]; representative: string } & GroupChartInteractiveProps) {
  const isDark = useIsDark();
  const interactive = Boolean(onEnter || onSelect);
  const renderBuy = interactive
    ? (props: ClusterMarkerProps) => <BuyClusterMarker {...props} onEnter={onEnter} onMove={onMove} onLeave={onLeave} onSelect={onSelect} selectedKey={selectedKey} />
    : BuyClusterMarker;
  const renderSell = interactive
    ? (props: ClusterMarkerProps) => <SellClusterMarker {...props} onEnter={onEnter} onMove={onMove} onLeave={onLeave} onSelect={onSelect} selectedKey={selectedKey} />
    : SellClusterMarker;
  const tooltipContent = (props: TooltipContentProps) => <GroupTooltip {...props} representative={representative} />;
  return (
    <MarkerChart
      data={data}
      yTickFormatter={fmtCurrencyShort}
      yWidth={60}
      hideXAxis
      tooltipContent={tooltipContent}
      tooltipWrapperStyle={tooltipWrapperStyle}
    >
      <Line type="monotone" dataKey="price" stroke={isDark ? "#60a5fa" : "#2563eb"} strokeWidth={2} dot={false} isAnimationActive={false} />
      {/* Sell first, Buy second — Buy paints on top (matches ticker-dialog ordering) */}
      <Scatter dataKey="sellClusterPrice" shape={renderSell} legendType="none" isAnimationActive={false} />
      <Scatter dataKey="buyClusterPrice" shape={renderBuy} legendType="none" isAnimationActive={false} />
    </MarkerChart>
  );
}
