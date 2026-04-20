"use client";

// ── Near-fullscreen ticker dialog (price chart + transaction table) ─────

import { useEffect, useRef, useState } from "react";
import { Line, Scatter, ReferenceLine } from "recharts";
import { ChartDialog } from "./chart-dialog";
import { MarkerChart } from "./marker-chart";
import { PriceTooltip } from "./ticker-chart-base";
import { fmtCurrency, fmtDateMedium, fmtQty } from "@/lib/format/format";
import { useIsDark, useIsMobile } from "@/lib/hooks/hooks";
import { tooltipStyle } from "@/lib/format/chart-styles";
import type { TickerTransaction } from "@/lib/schemas";
import { buildClusteredData, tsToIsoLocal, type TickerChartPoint } from "@/lib/format/ticker-data";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";
import {
  BuyClusterMarker,
  SellClusterMarker,
  ReinvestMarker,
  type ClusterMarkerProps,
  type HoverState,
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
  const [hover, setHover] = useState<HoverState | null>(null);

  const handleEnter = (h: HoverState) => setHover(h);
  const handleMove = (x: number, y: number) => setHover((prev) => (prev ? { ...prev, x, y } : null));
  const handleLeave = () => setHover(null);

  const selectedKey = selected?.key ?? null;
  const renderBuy = (props: ClusterMarkerProps) => (
    <BuyClusterMarker {...props} onEnter={handleEnter} onMove={handleMove} onLeave={handleLeave} onSelect={onSelect} selectedKey={selectedKey} />
  );
  const renderSell = (props: ClusterMarkerProps) => (
    <SellClusterMarker {...props} onEnter={handleEnter} onMove={handleMove} onLeave={handleLeave} onSelect={onSelect} selectedKey={selectedKey} />
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
          {avgCost != null && (
            <ReferenceLine
              y={avgCost}
              stroke={isDark ? "rgba(255,255,255,0.25)" : "rgba(0,0,0,0.2)"}
              strokeDasharray="4 4"
              label={{
                value: `Cost ${fmtCurrency(avgCost)}`,
                position: "insideTopRight",
                fill: isDark ? "rgba(255,255,255,0.55)" : "rgba(0,0,0,0.45)",
                fontSize: 10,
              }}
            />
          )}
        </MarkerChart>
      </div>
      {hover && (
        <div
          style={{
            ...tooltipStyle(isDark),
            position: "fixed",
            top: hover.y + 14,
            left: hover.x + 14,
            pointerEvents: "none",
            zIndex: 100,
          }}
        >
          <p style={{ fontWeight: 600, marginBottom: 2 }}>{fmtDateMedium(hover.dayIso)}</p>
          <p style={{ margin: 0 }}>Close: {fmtCurrency(hover.close)}</p>
          <p style={{ color: hover.side === "buy" ? BUY_COLOR : SELL_COLOR, margin: 0 }}>
            {hover.side === "buy" ? "Buy" : "Sell"}
            {hover.cluster.count > 1 ? ` ×${hover.cluster.count}` : ""}
            : {fmtQty(hover.cluster.qty)} @ {fmtCurrency(hover.cluster.price)} = {fmtCurrency(hover.cluster.amount)}
            {hover.cluster.count > 1 && (
              <> <span style={{ opacity: 0.7 }}>(around {fmtDateMedium(tsToIsoLocal(hover.cluster.ts))})</span></>
            )}
          </p>
        </div>
      )}
    </div>
  );
}

// ── Transaction table ──────────────────────────────────────────────────
// Single column on narrow screens, two transactions per row (to halve
// height) once the dialog is wide enough to give each column headroom.

function TransactionTable({
  transactions,
  selected,
  tableScrollRef,
  isDark,
}: {
  transactions: TickerTransaction[];
  selected: Selection | null;
  tableScrollRef: React.RefObject<HTMLDivElement | null>;
  isDark: boolean;
}) {
  const isMobile = useIsMobile();
  if (transactions.length === 0) return null;

  const selectedDateSet = selected ? new Set(selected.dates) : null;
  const highlightBg = selected?.side === "sell"
    ? (isDark ? "bg-amber-900/30" : "bg-amber-100")
    : (isDark ? "bg-emerald-900/30" : "bg-emerald-100");

  const emptyCells = (
    <>
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
      <td className="px-2 py-1.5" />
    </>
  );

  const renderCells = (t: TickerTransaction | null) => {
    if (!t) return emptyCells;
    const sideMatches = selected
      ? selected.side === "sell"
        ? t.actionType === "sell"
        : t.actionType === "buy" || t.actionType === "reinvestment"
      : false;
    const isHighlighted = sideMatches && (selectedDateSet?.has(t.runDate) ?? false);
    const bg = isHighlighted ? highlightBg : "";
    const dataSide = t.actionType === "sell" ? "sell" : "buy";
    return (
      <>
        <td data-date={t.runDate} data-side={dataSide} className={`px-2 py-1.5 ${bg}`}>{fmtDateMedium(t.runDate)}</td>
        {/* Arbitrary-value Tailwind classes must be literal strings so the JIT can extract them */}
        <td className={`px-2 py-1.5 capitalize ${bg} ${t.actionType === "sell" ? "text-[#E69F00]" : "text-[#009E73]"}`}>{t.actionType}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtQty(Math.abs(t.quantity))}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(t.price)}</td>
        <td className={`px-2 py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(Math.abs(t.amount))}</td>
      </>
    );
  };

  const headerGroup = (
    <>
      <th className="text-left px-2 py-1.5 font-medium">Date</th>
      <th className="text-left px-2 py-1.5 font-medium">Type</th>
      <th className="text-right px-2 py-1.5 font-medium">Qty</th>
      <th className="text-right px-2 py-1.5 font-medium">Price</th>
      <th className="text-right px-2 py-1.5 font-medium">Amount</th>
    </>
  );

  const rowBase = `border-b ${isDark ? "border-zinc-800" : "border-zinc-100"} transition-colors`;
  const headRowClass = `text-xs ${isDark ? "text-zinc-400" : "text-zinc-500"} border-b ${isDark ? "border-zinc-700" : "border-zinc-200"}`;

  const body = isMobile
    ? transactions.map((t, i) => (
        <tr key={i} className={rowBase}>{renderCells(t)}</tr>
      ))
    : (() => {
        const pairs: [TickerTransaction, TickerTransaction | null][] = [];
        for (let i = 0; i < transactions.length; i += 2) {
          pairs.push([transactions[i], transactions[i + 1] ?? null]);
        }
        return pairs.map(([a, b], i) => (
          <tr key={i} className={rowBase}>
            {renderCells(a)}
            <td className="w-6" />
            {renderCells(b)}
          </tr>
        ));
      })();

  return (
    <div ref={tableScrollRef} className="shrink-0 max-h-[40%] overflow-auto px-3 sm:px-5 pb-4 border-t border-foreground/10">
      <table className="w-full text-sm whitespace-nowrap">
        <thead>
          <tr className={headRowClass}>
            {headerGroup}
            {!isMobile && (
              <>
                <th className="w-6" />
                {headerGroup}
              </>
            )}
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
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
  const isDark = useIsDark();
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
        isDark={isDark}
      />
    </ChartDialog>
  );
}
