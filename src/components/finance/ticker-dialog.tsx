"use client";

// ── Near-fullscreen ticker dialog (price chart + transaction table) ─────

import { useEffect, useRef, useState } from "react";
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
import { fmtCurrency, fmtDateMedium, fmtQty, fmtTick } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/chart-styles";
import { TooltipCard } from "@/components/charts/tooltip-card";
import type { TickerTransaction } from "@/lib/schemas";
import {
  buildClusteredData,
  tsToIsoLocal,
  type TickerChartPoint,
  type ClusteredPoint,
  type Cluster,
} from "@/lib/ticker-data";
import { BUY_COLOR, SELL_COLOR } from "@/lib/chart-colors";
import {
  BuyClusterMarker,
  SellClusterMarker,
  type ClusterMarkerProps,
  type HoverState,
  type Selection,
} from "./ticker-markers";

function DialogPriceTooltip({ active, payload }: TooltipContentProps) {
  const d = payload?.[0]?.payload as ClusteredPoint | undefined;
  if (!d) return null;

  const clusterLine = (c: Cluster, label: string, color: string) => {
    const tag = c.count > 1 ? ` ×${c.count}` : "";
    return (
      <p style={{ color, margin: 0 }}>
        {label}{tag}: {fmtQty(c.qty)} @ {fmtCurrency(c.price)} = {fmtCurrency(c.amount)}
        {c.count > 1 && (
          <> <span style={{ opacity: 0.7 }}>(around {fmtDateMedium(tsToIsoLocal(c.ts))})</span></>
        )}
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
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={clusteredData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
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
              tickFormatter={(v: number) => `$${v}`}
              width={55}
              {...axisProps(isDark)}
              axisLine={false}
            />
            <Tooltip content={DialogPriceTooltip} wrapperStyle={hover ? { visibility: "hidden" } : undefined} />
            <Line
              type="monotone"
              dataKey="close"
              stroke={isDark ? "#60a5fa" : "#2563eb"}
              strokeWidth={1.5}
              dot={false}
              activeDot={false}
              isAnimationActive={false}
            />
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
          </ComposedChart>
        </ResponsiveContainer>
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
  const dialogRef = useRef<HTMLDialogElement>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);
  const isDark = useIsDark();
  const [selected, setSelected] = useState<Selection | null>(null);

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    el.showModal();
    const onCancel = (e: Event) => { e.preventDefault(); onClose(); };
    el.addEventListener("cancel", onCancel);
    // Lock body scroll while modal is open — <dialog> modal doesn't block wheel propagation on its own
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      el.removeEventListener("cancel", onCancel);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  useEffect(() => {
    if (!selected || !tableScrollRef.current) return;
    // Scroll the most-recent member (sorted descending, so first match = latest) into view
    const cell = tableScrollRef.current.querySelector<HTMLElement>(`td[data-date="${selected.dates[0]}"][data-side="${selected.side}"]`);
    cell?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected]);

  const sorted = [...transactions].sort((a, b) => b.runDate.localeCompare(a.runDate));

  return (
    <dialog
      ref={dialogRef}
      onClick={(e) => {
        e.stopPropagation();
        if (e.target === dialogRef.current) onClose();
      }}
      className="fixed inset-0 m-auto backdrop:bg-black/50 backdrop:backdrop-blur-sm bg-transparent p-0 max-w-none max-h-none border-0 overflow-visible"
    >
      <div className={`${isDark ? "bg-zinc-900 text-zinc-100" : "bg-white text-zinc-900"} rounded-xl shadow-2xl flex flex-col resize overflow-hidden w-[95vw] h-[92vh] min-w-[400px] min-h-[300px] max-w-[99vw] max-h-[98vh]`}>
        {/* Header */}
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-foreground/10">
          <span className="font-semibold text-lg font-mono">{symbol}</span>
          <button
            onClick={onClose}
            aria-label="Close"
            className={`w-8 h-8 flex items-center justify-center rounded-full text-2xl leading-none ${isDark ? "hover:bg-zinc-800 text-zinc-300 hover:text-zinc-50" : "hover:bg-zinc-100 text-zinc-500 hover:text-zinc-900"} transition-colors`}
          >
            &times;
          </button>
        </div>
        {/* Chart */}
        <div className="flex-1 min-h-0 px-4 pt-4 pb-2">
          <TickerDialogChart data={data} avgCost={avgCost} selected={selected} onSelect={setSelected} />
        </div>
        {/* Transaction table */}
        <TransactionTable
          transactions={sorted}
          selected={selected}
          tableScrollRef={tableScrollRef}
          isDark={isDark}
        />
      </div>
    </dialog>
  );
}
