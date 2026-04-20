"use client";

// ── Near-fullscreen ticker dialog (price chart + transaction table) ─────

import { useEffect, useRef, useState } from "react";
import { useHoverState } from "@/lib/hooks/use-hover-state";
import { Line, Scatter } from "recharts";
import { ChartDialog } from "./chart-dialog";
import { MarkerChart } from "./marker-chart";
import { AvgCostReferenceLine, PriceTooltip } from "./ticker-chart-base";
import { useIsDark } from "@/lib/hooks/hooks";
import type { TickerTransaction } from "@/lib/schemas";
import { buildClusteredData, type TickerChartPoint } from "@/lib/format/ticker-data";
import { TransactionTable } from "./transaction-table";
import { MarkerHoverPanel } from "./marker-hover-panel";
import {
  BuyClusterMarker,
  SellClusterMarker,
  ReinvestMarker,
  type ClusterMarkerProps,
  type Selection,
} from "./ticker-markers";

function TickerDialogChart({
  data,
  avgCost,
  selected,
  onSelect,
}: {
  data: TickerChartPoint[];
  avgCost: number | null;
  selected: Selection | null;
  onSelect: (sel: Selection | null) => void;
}) {
  const isDark = useIsDark();
  const clusteredData = buildClusteredData(data);
  const { hover, onEnter, onMove, onLeave } = useHoverState();

  const selectedKey = selected?.key ?? null;
  const renderBuy = (props: ClusterMarkerProps) => (
    <BuyClusterMarker {...props} onEnter={onEnter} onMove={onMove} onLeave={onLeave} onSelect={onSelect} selectedKey={selectedKey} />
  );
  const renderSell = (props: ClusterMarkerProps) => (
    <SellClusterMarker {...props} onEnter={onEnter} onMove={onMove} onLeave={onLeave} onSelect={onSelect} selectedKey={selectedKey} />
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 min-h-0">
        <MarkerChart
          data={clusteredData}
          yTickFormatter={(v) => `$${v}`}
          hideXAxis
          tooltipContent={PriceTooltip}
          tooltipWrapperStyle={hover ? { visibility: "hidden" } : undefined}
        >
          <Line type="monotone" dataKey="close" stroke={isDark ? "#60a5fa" : "#2563eb"} strokeWidth={1.5} dot={false} activeDot={false} isAnimationActive={false} />
          {/* Reinvest dots first (paint underneath), then Sell, then Buy on top */}
          <Scatter dataKey="reinvestDot" shape={ReinvestMarker} legendType="none" isAnimationActive={false} />
          {/* Sell first, Buy second — Buy paints on top so click hit-testing prefers the larger/more-frequent buy cluster when a same-date sell overlaps */}
          <Scatter dataKey="sellClusterPrice" shape={renderSell} legendType="none" isAnimationActive={false} />
          <Scatter dataKey="buyClusterPrice" shape={renderBuy} legendType="none" isAnimationActive={false} />
          <AvgCostReferenceLine avgCost={avgCost} labelText="Cost" labelPosition="insideTopRight" />
        </MarkerChart>
      </div>
      {hover && <MarkerHoverPanel hover={hover} isDark={isDark} />}
    </div>
  );
}

// ── Dialog shell ────────────────────────────────────────────────────────

export function TickerChartDialog({
  symbol,
  data,
  avgCost,
  transactions,
  onClose,
}: {
  symbol: string;
  data: TickerChartPoint[];
  avgCost: number | null;
  transactions: TickerTransaction[];
  onClose: () => void;
}) {
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const [selected, setSelected] = useState<Selection | null>(null);

  useEffect(() => {
    if (!selected || !tableScrollRef.current) return;
    // Scroll the most-recent member (sorted descending, so first match = latest) into view
    const cell = tableScrollRef.current.querySelector<HTMLElement>(`td[data-date="${selected.dates[0]}"][data-side="${selected.side}"]`);
    cell?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected]);

  const sorted = [...transactions].sort((a, b) => b.runDate.localeCompare(a.runDate));

  return (
    <ChartDialog
      header={<span className="font-semibold text-lg font-mono">{symbol}</span>}
      onClose={onClose}
    >
      <div className="flex-1 min-h-0 px-4 pt-4 pb-2">
        <TickerDialogChart data={data} avgCost={avgCost} selected={selected} onSelect={setSelected} />
      </div>
      <TransactionTable
        transactions={sorted}
        selected={selected}
        tableScrollRef={tableScrollRef}
      />
    </ChartDialog>
  );
}
