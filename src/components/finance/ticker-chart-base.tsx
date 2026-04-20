"use client";

// ── Inline ticker chart (non-dialog) ─────────────────────────────────────
//
// Small-format price chart rendered inside the activity-table row. Markers
// are one-per-day (no clustering) and the tooltip is the simple single-day
// variant. The larger dialog chart lives in ticker-dialog.tsx.

import { Line, Scatter, ReferenceLine } from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import { fmtCurrency, fmtDateMedium, fmtQty } from "@/lib/format/format";
import { useIsDark } from "@/lib/hooks/hooks";
import { buildClusteredData, tsToIsoLocal, type Cluster, type ClusteredPoint } from "@/lib/format/ticker-data";
import type { TickerChartPoint } from "@/lib/format/ticker-data";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";
import { TooltipCard } from "@/components/charts/tooltip-card";
import { BuyClusterMarker, SellClusterMarker, ReinvestMarker } from "./ticker-markers";
import { MarkerChart } from "./marker-chart";

export function AvgCostReferenceLine({
  avgCost,
  labelText,
  labelPosition,
}: {
  avgCost: number | null;
  labelText: string;
  labelPosition: "right" | "insideTopRight";
}) {
  const isDark = useIsDark();
  if (avgCost == null) return null;
  const fillOpacity = labelPosition === "insideTopRight" ? (isDark ? 0.55 : 0.45) : (isDark ? 0.5 : 0.4);
  return (
    <ReferenceLine
      y={avgCost}
      stroke={isDark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.2)"}
      strokeDasharray="4 4"
      label={{
        value: `${labelText} ${fmtCurrency(avgCost)}`,
        position: labelPosition,
        fill: isDark ? `rgba(255,255,255,${fillOpacity})` : `rgba(0,0,0,${fillOpacity})`,
        fontSize: 10,
      }}
    />
  );
}

/** Shared price-tooltip for per-ticker charts (inline + dialog). */
export function PriceTooltip({ active, payload }: TooltipContentProps) {
  const d = payload?.[0]?.payload as ClusteredPoint | undefined;
  if (!d) return null;
  const clusterLine = (c: Cluster, label: string, color: string) => {
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
    <MarkerChart
      data={clustered}
      height={height}
      yTickFormatter={(v) => `$${v}`}
      tooltipContent={PriceTooltip}
    >
      <Line type="monotone" dataKey="close" stroke={isDark ? "#60a5fa" : "#2563eb"} strokeWidth={1.5} dot={false} isAnimationActive={false} />
      <Scatter dataKey="reinvestDot" shape={ReinvestMarker} legendType="none" isAnimationActive={false} />
      <Scatter dataKey="sellClusterPrice" shape={SellClusterMarker} legendType="none" isAnimationActive={false} />
      <Scatter dataKey="buyClusterPrice" shape={BuyClusterMarker} legendType="none" isAnimationActive={false} />
      <AvgCostReferenceLine avgCost={avgCost} labelText="Avg" labelPosition="right" />
    </MarkerChart>
  );
}
