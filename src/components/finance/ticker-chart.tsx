"use client";

import { useEffect, useState } from "react";
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
import { TickerPriceResponseSchema, type TickerPricePoint, type TickerTransaction } from "@/lib/schemas";
import { fmtCurrency, fmtDateMedium, fmtQty, fmtTick } from "@/lib/format";
import { getIsDark, useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/chart-styles";
import { useRef } from "react";

// ── Data merging ──────────────────────────────────────────────────────────

export type TickerChartPoint = {
  date: string;
  ts: number;
  close: number;
  buyPrice?: number;      // VWAP across all buy txns on this date
  buyQty?: number;
  buyAmount?: number;
  buyTxnCount?: number;   // number of underlying buy/reinvestment transactions
  sellPrice?: number;
  sellQty?: number;
  sellAmount?: number;
  sellTxnCount?: number;
};


export function mergeTickerData(
  prices: TickerPricePoint[],
  transactions: TickerTransaction[],
): TickerChartPoint[] {
  // Index transactions by ISO date (qty/amount summed; price becomes VWAP below)
  const buyMap = new Map<string, { qty: number; amount: number; count: number }>();
  const sellMap = new Map<string, { qty: number; amount: number; count: number }>();

  for (const t of transactions) {
    const iso = t.runDate; // ISO YYYY-MM-DD — normalized upstream
    const qty = Math.abs(t.quantity);
    const amount = Math.abs(t.amount);
    if (t.actionType === "buy" || t.actionType === "reinvestment") {
      const existing = buyMap.get(iso);
      if (existing) {
        existing.qty += qty;
        existing.amount += amount;
        existing.count += 1;
      } else {
        buyMap.set(iso, { qty, amount, count: 1 });
      }
    } else if (t.actionType === "sell") {
      const existing = sellMap.get(iso);
      if (existing) {
        existing.qty += qty;
        existing.amount += amount;
        existing.count += 1;
      } else {
        sellMap.set(iso, { qty, amount, count: 1 });
      }
    }
  }

  return prices.map((p) => {
    const [y, m, d] = p.date.split("-");
    const ts = new Date(+y, +m - 1, +d).getTime();
    const point: TickerChartPoint = { date: p.date, ts, close: p.close };
    const buy = buyMap.get(p.date);
    if (buy) {
      point.buyPrice = buy.qty > 0 ? buy.amount / buy.qty : 0;
      point.buyQty = buy.qty;
      point.buyAmount = buy.amount;
      point.buyTxnCount = buy.count;
    }
    const sell = sellMap.get(p.date);
    if (sell) {
      point.sellPrice = sell.qty > 0 ? sell.amount / sell.qty : 0;
      point.sellQty = sell.qty;
      point.sellAmount = sell.amount;
      point.sellTxnCount = sell.count;
    }
    return point;
  });
}

// ── Scatter markers (B/S labels — color-blind friendly, don't rely on hue alone) ─

type MarkerProps = { cx?: number; cy?: number };

function BuyMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  return (
    <g>
      <circle cx={cx} cy={cy} r={9} fill="#009E73" />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={11} fontWeight={700}>B</text>
    </g>
  );
}

function SellMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  const r = 9;
  return (
    <g>
      <path d={`M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`} fill="#E69F00" />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={11} fontWeight={700}>S</text>
    </g>
  );
}

// ── Marker clustering (for dense dialog chart) ────────────────────────────

type Cluster = {
  ts: number;              // weighted-by-amount centroid timestamp
  price: number;           // VWAP across members
  qty: number;             // sum of abs qty
  amount: number;          // sum of abs amount (USD)
  count: number;           // total number of underlying transactions merged
  r: number;               // render radius (px) — filled in after normalization
  memberDates: string[];   // ISO dates of merged days (used to highlight table rows)
};

function clusterByTime(
  points: TickerChartPoint[],
  priceField: "buyPrice" | "sellPrice",
  qtyField: "buyQty" | "sellQty",
  amountField: "buyAmount" | "sellAmount",
  countField: "buyTxnCount" | "sellTxnCount",
): Cluster[] {
  type M = { ts: number; price: number; qty: number; amount: number; count: number; date: string };
  const markers: M[] = [];
  for (const p of points) {
    const price = p[priceField];
    const qty = p[qtyField];
    const amount = p[amountField];
    const count = p[countField];
    if (price == null || qty == null || amount == null || count == null) continue;
    markers.push({ ts: p.ts, price, qty, amount, count, date: p.date });
  }
  if (markers.length === 0 || points.length < 2) return [];
  markers.sort((a, b) => a.ts - b.ts);

  const span = points[points.length - 1].ts - points[0].ts;
  const threshold = span * 0.015; // merge if within 1.5% of visible range

  const finalize = (bucket: M[]): Cluster => {
    const qty = bucket.reduce((s, m) => s + m.qty, 0);
    const amount = bucket.reduce((s, m) => s + m.amount, 0);
    const count = bucket.reduce((s, m) => s + m.count, 0);
    // centroid weighted by amount (big trades pull the marker to their date)
    const ts = bucket.reduce((s, m) => s + m.ts * m.amount, 0) / amount;
    const price = amount / qty; // VWAP
    const memberDates = bucket.map((m) => m.date);
    return { ts, price, qty, amount, count, r: 0, memberDates };
  };

  const clusters: Cluster[] = [];
  let bucket: M[] = [markers[0]];
  for (let i = 1; i < markers.length; i++) {
    if (markers[i].ts - bucket[bucket.length - 1].ts < threshold) {
      bucket.push(markers[i]);
    } else {
      clusters.push(finalize(bucket));
      bucket = [markers[i]];
    }
  }
  clusters.push(finalize(bucket));
  return clusters;
}

function sizeClusters(buys: Cluster[], sells: Cluster[]): { buys: Cluster[]; sells: Cluster[] } {
  const maxAmount = Math.max(1, ...buys.map((c) => c.amount), ...sells.map((c) => c.amount));
  const MIN_R = 9;
  const MAX_R = 22;
  const withR = (c: Cluster): Cluster => ({
    ...c,
    r: MIN_R + (MAX_R - MIN_R) * Math.sqrt(c.amount / maxAmount),
  });
  return { buys: buys.map(withR), sells: sells.map(withR) };
}

function ClusterCountBadge({ cx, cy, r, count, color }: { cx: number; cy: number; r: number; count: number; color: string }) {
  if (count <= 1) return null;
  // Position badge just outside the NE of the marker
  const offsetX = r * 0.75;
  const offsetY = -r * 0.9;
  return (
    <text
      x={cx + offsetX}
      y={cy + offsetY}
      textAnchor="start"
      fill={color}
      stroke="white"
      strokeWidth={3}
      paintOrder="stroke fill"
      fontSize={12}
      fontWeight={800}
    >
      ×{count}
    </text>
  );
}

type HoverState = {
  cluster: Cluster;
  side: "buy" | "sell";
  dayIso: string;
  close: number;
  x: number;
  y: number;
};

type Selection = { key: string; dates: string[]; side: "buy" | "sell" };

function clusterKey(side: "buy" | "sell", c: Cluster): string {
  return `${side}-${c.ts}-${c.count}`;
}

type ClusterMarkerProps = MarkerProps & {
  payload?: { buyCluster?: Cluster; sellCluster?: Cluster; date?: string; close?: number };
  onEnter?: (h: HoverState) => void;
  onMove?: (x: number, y: number) => void;
  onLeave?: () => void;
  onSelect?: (sel: Selection | null) => void;
  selectedKey?: string | null;
};

function BuyClusterMarker({ cx, cy, payload, onEnter, onMove, onLeave, onSelect, selectedKey }: ClusterMarkerProps) {
  const c = payload?.buyCluster;
  if (cx == null || cy == null || !c) return null;
  const { r, count } = c;
  const fontSize = Math.max(9, Math.min(r * 1.1, 13));
  const key = clusterKey("buy", c);
  const isSelected = selectedKey === key;
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "buy", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.(isSelected ? null : { key, dates: c.memberDates, side: "buy" });
      }}
      style={{ cursor: "pointer" }}
    >
      {isSelected && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke="#009E73" strokeWidth={2} />}
      <circle cx={cx} cy={cy} r={r} fill="#009E73" />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={fontSize} fontWeight={700} pointerEvents="none">B</text>
      <ClusterCountBadge cx={cx} cy={cy} r={r} count={count} color="#009E73" />
    </g>
  );
}

function SellClusterMarker({ cx, cy, payload, onEnter, onMove, onLeave, onSelect, selectedKey }: ClusterMarkerProps) {
  const c = payload?.sellCluster;
  if (cx == null || cy == null || !c) return null;
  const { r, count } = c;
  const fontSize = Math.max(9, Math.min(r * 1.1, 13));
  const key = clusterKey("sell", c);
  const isSelected = selectedKey === key;
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "sell", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.(isSelected ? null : { key, dates: c.memberDates, side: "sell" });
      }}
      style={{ cursor: "pointer" }}
    >
      {isSelected && (
        <path
          d={`M ${cx} ${cy - r - 4} L ${cx + r + 4} ${cy} L ${cx} ${cy + r + 4} L ${cx - r - 4} ${cy} Z`}
          fill="none"
          stroke="#E69F00"
          strokeWidth={2}
        />
      )}
      <path d={`M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`} fill="#E69F00" />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={fontSize} fontWeight={700} pointerEvents="none">S</text>
      <ClusterCountBadge cx={cx} cy={cy} r={r} count={count} color="#E69F00" />
    </g>
  );
}

// ── Tooltip ───────────────────────────────────────────────────────────────

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
        <p style={{ color: "#009E73", margin: 0 }}>
          Buy: {d.buyQty} × {fmtCurrency(d.buyPrice)} = {fmtCurrency(d.buyAmount!)}
        </p>
      )}
      {d.sellPrice != null && (
        <p style={{ color: "#E69F00", margin: 0 }}>
          Sell: {d.sellQty} × {fmtCurrency(d.sellPrice)} = {fmtCurrency(d.sellAmount!)}
        </p>
      )}
    </div>
  );
}

// ── Chart component ───────────────────────────────────────────────────────

function TickerChartInner({ data, avgCost, height = 200 }: { data: TickerChartPoint[]; avgCost: number | null; height?: number }) {
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
        <Scatter
          dataKey="buyPrice"
          shape={BuyMarker}
          legendType="none"
          isAnimationActive={false}
        />
        <Scatter
          dataKey="sellPrice"
          shape={SellMarker}
          legendType="none"
          isAnimationActive={false}
        />
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

// ── Dialog chart ──────────────────────────────────────────────────────────

type ClusteredPoint = TickerChartPoint & {
  buyClusterPrice?: number;
  buyCluster?: Cluster;
  sellClusterPrice?: number;
  sellCluster?: Cluster;
};

function tsToIsoLocal(ts: number): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function buildClusteredData(data: TickerChartPoint[]): ClusteredPoint[] {
  const sized = sizeClusters(
    clusterByTime(data, "buyPrice", "buyQty", "buyAmount", "buyTxnCount"),
    clusterByTime(data, "sellPrice", "sellQty", "sellAmount", "sellTxnCount"),
  );

  // Build the dialog-only data array: drop per-day scatter markers (replaced by cluster markers)
  const out: ClusteredPoint[] = data.map((d) => ({
    ...d,
    buyPrice: undefined,
    sellPrice: undefined,
  }));

  if (out.length === 0) return out;

  const tsList = out.map((d) => d.ts);
  const nearestIdx = (ts: number): number => {
    let lo = 0, hi = tsList.length - 1;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (tsList[mid] < ts) lo = mid + 1; else hi = mid;
    }
    if (lo === 0) return 0;
    return Math.abs(ts - tsList[lo - 1]) < Math.abs(ts - tsList[lo]) ? lo - 1 : lo;
  };

  for (const c of sized.buys) {
    const i = nearestIdx(c.ts);
    out[i] = { ...out[i], buyClusterPrice: c.price, buyCluster: c };
  }
  for (const c of sized.sells) {
    const i = nearestIdx(c.ts);
    out[i] = { ...out[i], sellClusterPrice: c.price, sellCluster: c };
  }
  return out;
}

function DialogPriceTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const isDark = getIsDark();
  const style = tooltipStyle(isDark);
  const d = payload[0]?.payload as ClusteredPoint | undefined;
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
    <div style={style}>
      <p style={{ fontWeight: 600, marginBottom: 2 }}>{fmtDateMedium(d.date)}</p>
      <p style={{ margin: 0 }}>Close: {fmtCurrency(d.close)}</p>
      {d.buyCluster && clusterLine(d.buyCluster, "Buy", "#009E73")}
      {d.sellCluster && clusterLine(d.sellCluster, "Sell", "#E69F00")}
    </div>
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
            <Scatter
              dataKey="sellClusterPrice"
              shape={renderSell}
              legendType="none"
              isAnimationActive={false}
            />
            <Scatter
              dataKey="buyClusterPrice"
              shape={renderBuy}
              legendType="none"
              isAnimationActive={false}
            />
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
          <p style={{ color: hover.side === "buy" ? "#009E73" : "#E69F00", margin: 0 }}>
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

// ── Dialog ────────────────────────────────────────────────────────────────

function TickerChartDialog({
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
  const selectedDateSet = selected ? new Set(selected.dates) : null;
  const highlightBg = selected?.side === "sell"
    ? (isDark ? "bg-amber-900/30" : "bg-amber-100")
    : (isDark ? "bg-emerald-900/30" : "bg-emerald-100");

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
        {/* Transaction table — 2 transactions per visual row to halve vertical space */}
        {sorted.length > 0 && (() => {
          const renderCells = (t: TickerTransaction | null) => {
            if (!t) {
              return (
                <>
                  <td className="py-1.5" />
                  <td className="py-1.5" />
                  <td className="py-1.5" />
                  <td className="py-1.5" />
                  <td className="py-1.5" />
                </>
              );
            }
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
                <td data-date={t.runDate} data-side={dataSide} className={`py-1.5 ${bg}`}>{fmtDateMedium(t.runDate)}</td>
                <td className={`py-1.5 capitalize ${bg} ${t.actionType === "sell" ? "text-[#E69F00]" : "text-[#009E73]"}`}>{t.actionType}</td>
                <td className={`py-1.5 text-right font-mono ${bg}`}>{fmtQty(Math.abs(t.quantity))}</td>
                <td className={`py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(t.price)}</td>
                <td className={`py-1.5 text-right font-mono ${bg}`}>{fmtCurrency(Math.abs(t.amount))}</td>
              </>
            );
          };
          const pairs: [TickerTransaction, TickerTransaction | null][] = [];
          for (let i = 0; i < sorted.length; i += 2) {
            pairs.push([sorted[i], sorted[i + 1] ?? null]);
          }
          const headerGroup = (
            <>
              <th className="text-left py-1.5 font-medium">Date</th>
              <th className="text-left py-1.5 font-medium">Type</th>
              <th className="text-right py-1.5 font-medium">Qty</th>
              <th className="text-right py-1.5 font-medium">Price</th>
              <th className="text-right py-1.5 font-medium">Amount</th>
            </>
          );
          return (
            <div ref={tableScrollRef} className="shrink-0 max-h-[40%] overflow-y-auto px-5 pb-4 border-t border-foreground/10">
              <table className="w-full text-sm">
                <thead>
                  <tr className={`text-xs ${isDark ? "text-zinc-400" : "text-zinc-500"} border-b ${isDark ? "border-zinc-700" : "border-zinc-200"}`}>
                    {headerGroup}
                    <th className="w-6" />
                    {headerGroup}
                  </tr>
                </thead>
                <tbody>
                  {pairs.map(([a, b], i) => (
                    <tr
                      key={i}
                      className={`border-b ${isDark ? "border-zinc-800" : "border-zinc-100"} transition-colors`}
                    >
                      {renderCells(a)}
                      <td className="w-6" />
                      {renderCells(b)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })()}
      </div>
    </dialog>
  );
}

// ── Fetching wrapper ──────────────────────────────────────────────────────

import { WORKER_BASE } from "@/lib/config";

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const [data, setData] = useState<TickerChartPoint[] | null>(null);
  const [avgCost, setAvgCost] = useState<number | null>(null);
  const [transactions, setTransactions] = useState<TickerTransaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${WORKER_BASE}/prices/${encodeURIComponent(symbol)}`);
        if (!res.ok) throw new Error(`${res.status}`);
        const json = await res.json();
        const parsed = TickerPriceResponseSchema.safeParse(json);
        if (!parsed.success) {
          throw new Error(`schema drift: ${parsed.error.issues[0]?.message ?? "unknown"}`);
        }
        if (cancelled) return;

        const { prices, transactions: txns } = parsed.data;
        const merged = mergeTickerData(prices, txns);
        setData(merged);
        setTransactions(txns);

        // Compute average cost basis from buy transactions
        const buys = txns.filter(
          (t) => t.actionType === "buy" || t.actionType === "reinvestment",
        );
        const totalCost = buys.reduce((s, t) => s + Math.abs(t.amount), 0);
        const totalQty = buys.reduce((s, t) => s + Math.abs(t.quantity), 0);
        setAvgCost(totalQty > 0 ? totalCost / totalQty : null);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load");
      }
    })();
    return () => { cancelled = true; };
  }, [symbol]);

  if (error) return <p className="text-xs text-red-400 py-2">Failed to load chart: {error}</p>;
  if (!data) return <p className="text-xs text-muted-foreground py-2 animate-pulse">Loading {symbol} chart...</p>;
  if (data.length === 0) {
    const isMM = /^(SPAXX|FDRXX|FZFXX|FCASH)$/.test(symbol);
    const msg = isMM ? "Money market fund \u2014 price fixed at $1.00" : `No price data for ${symbol}`;
    return <p className="text-xs text-muted-foreground py-2">{msg}</p>;
  }

  // Filter to global brush range
  const filtered = (startDate && endDate)
    ? data.filter((p) => p.date >= startDate && p.date <= endDate)
    : data;

  if (filtered.length === 0) {
    return <p className="text-xs text-muted-foreground py-2">No price data for {symbol} in selected range</p>;
  }

  return (
    <div
      className="cursor-zoom-in relative group"
      onClick={(e) => { e.stopPropagation(); setDialogOpen(true); }}
    >
      <TickerChartInner data={filtered} avgCost={avgCost} />
      <div className="absolute top-1 right-1 text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        &#x26F6;
      </div>
      {dialogOpen && (
        <TickerChartDialog
          symbol={symbol}
          data={filtered}
          avgCost={avgCost}
          transactions={transactions}
          onClose={() => setDialogOpen(false)}
        />
      )}
    </div>
  );
}
