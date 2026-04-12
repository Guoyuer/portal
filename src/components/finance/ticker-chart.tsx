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
import type { TickerPricePoint, TickerTransaction, TickerPriceResponse } from "@/lib/schema";
import { fmtCurrency, fmtDateMedium, fmtTick } from "@/lib/format";
import { fidelityDateToIso } from "@/lib/compute";
import { useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/chart-styles";
import { getIsDark } from "@/lib/style-helpers";

// ── Data merging ──────────────────────────────────────────────────────────

export type TickerChartPoint = {
  date: string;
  ts: number;
  close: number;
  buyPrice?: number;
  buyQty?: number;
  buyAmount?: number;
  sellPrice?: number;
  sellQty?: number;
  sellAmount?: number;
};


export function mergeTickerData(
  prices: TickerPricePoint[],
  transactions: TickerTransaction[],
): TickerChartPoint[] {
  // Index transactions by ISO date
  const buyMap = new Map<string, { price: number; qty: number; amount: number }>();
  const sellMap = new Map<string, { price: number; qty: number; amount: number }>();

  for (const t of transactions) {
    const iso = fidelityDateToIso(t.runDate);
    if (t.actionType === "buy" || t.actionType === "reinvestment") {
      const existing = buyMap.get(iso);
      if (existing) {
        existing.qty += Math.abs(t.quantity);
        existing.amount += Math.abs(t.amount);
      } else {
        buyMap.set(iso, { price: t.price, qty: Math.abs(t.quantity), amount: Math.abs(t.amount) });
      }
    } else if (t.actionType === "sell") {
      const existing = sellMap.get(iso);
      if (existing) {
        existing.qty += Math.abs(t.quantity);
        existing.amount += Math.abs(t.amount);
      } else {
        sellMap.set(iso, { price: t.price, qty: Math.abs(t.quantity), amount: Math.abs(t.amount) });
      }
    }
  }

  return prices.map((p) => {
    const [y, m, d] = p.date.split("-");
    const ts = new Date(+y, +m - 1, +d).getTime();
    const point: TickerChartPoint = { date: p.date, ts, close: p.close };
    const buy = buyMap.get(p.date);
    if (buy) {
      point.buyPrice = buy.price;
      point.buyQty = buy.qty;
      point.buyAmount = buy.amount;
    }
    const sell = sellMap.get(p.date);
    if (sell) {
      point.sellPrice = sell.price;
      point.sellQty = sell.qty;
      point.sellAmount = sell.amount;
    }
    return point;
  });
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
          Buy: {d.buyQty} × {fmtCurrency(d.buyPrice)} = {fmtCurrency(d.buyAmount ?? 0)}
        </p>
      )}
      {d.sellPrice != null && (
        <p style={{ color: "#E69F00", margin: 0 }}>
          Sell: {d.sellQty} × {fmtCurrency(d.sellPrice)} = {fmtCurrency(d.sellAmount ?? 0)}
        </p>
      )}
    </div>
  );
}

// ── Chart component ───────────────────────────────────────────────────────

function TickerChartInner({ data, avgCost }: { data: TickerChartPoint[]; avgCost: number | null }) {
  const isDark = useIsDark();

  return (
    <ResponsiveContainer width="100%" height={200}>
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
          fill="#009E73"
          shape="circle"
          legendType="none"
          isAnimationActive={false}
        />
        <Scatter
          dataKey="sellPrice"
          fill="#E69F00"
          shape="diamond"
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

// ── Fetching wrapper ──────────────────────────────────────────────────────

import { WORKER_BASE } from "@/lib/config";

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const [data, setData] = useState<TickerChartPoint[] | null>(null);
  const [avgCost, setAvgCost] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch(`${WORKER_BASE}/prices/${encodeURIComponent(symbol)}`);
        if (!res.ok) throw new Error(`${res.status}`);
        const json = (await res.json()) as TickerPriceResponse;
        if (cancelled) return;

        const merged = mergeTickerData(json.prices, json.transactions);
        setData(merged);

        // Compute average cost basis from buy transactions
        const buys = json.transactions.filter(
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

  return <TickerChartInner data={filtered} avgCost={avgCost} />;
}
