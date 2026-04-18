"use client";

// ── Ticker chart public entry ────────────────────────────────────────────
//
// Composes:
// - Pure data layer: @/lib/format/ticker-data (mergeTickerData, clusterByTime, ...)
// - Inline chart: ./ticker-chart-base  (TickerChartBase, non-interactive)
// - Dialog: ./ticker-dialog         (near-fullscreen modal with clustering)
//
// This file is kept small deliberately — it owns only the /prices/:symbol
// fetch, range-filtering, and the expand-to-dialog gesture.

import { useEffect, useState } from "react";
import { TickerPriceResponseSchema, type TickerTransaction } from "@/lib/schemas";
import { WORKER_BASE } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";
import { mergeTickerData, type TickerChartPoint } from "@/lib/format/ticker-data";
import { TickerChartBase } from "./ticker-chart-base";
import { TickerChartDialog } from "./ticker-dialog";

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const [data, setData] = useState<TickerChartPoint[] | null>(null);
  const [avgCost, setAvgCost] = useState<number | null>(null);
  const [transactions, setTransactions] = useState<TickerTransaction[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [dialogOpen, setDialogOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchWithSchema(`${WORKER_BASE}/prices/${encodeURIComponent(symbol)}`, TickerPriceResponseSchema)
      .then(({ prices, transactions: txns }) => {
        if (cancelled) return;
        setData(mergeTickerData(prices, txns));
        setTransactions(txns);
        // Average cost basis from buys + reinvestments
        const buys = txns.filter((t) => t.actionType === "buy" || t.actionType === "reinvestment");
        const totalCost = buys.reduce((s, t) => s + Math.abs(t.amount), 0);
        const totalQty = buys.reduce((s, t) => s + Math.abs(t.quantity), 0);
        setAvgCost(totalQty > 0 ? totalCost / totalQty : null);
      })
      .catch((e) => { if (!cancelled) setError(e instanceof Error ? e.message : "Failed to load"); });
    return () => { cancelled = true; };
  }, [symbol]);

  if (error) return <p className="text-xs text-red-400 py-2">Failed to load chart: {error}</p>;
  if (!data) return <p className="text-xs text-muted-foreground py-2 animate-pulse">Loading {symbol} chart...</p>;
  if (data.length === 0) {
    const isMM = /^(SPAXX|FDRXX|FZFXX|FCASH)$/.test(symbol);
    const msg = isMM ? "Money market fund \u2014 price fixed at $1.00" : `No price data for ${symbol}`;
    return <p className="text-xs text-muted-foreground py-2">{msg}</p>;
  }

  // Filter to the global brush range if provided
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
      <TickerChartBase data={filtered} avgCost={avgCost} />
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
