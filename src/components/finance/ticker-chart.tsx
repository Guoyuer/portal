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

type TickerData = {
  data: TickerChartPoint[] | null;
  avgCost: number | null;
  transactions: TickerTransaction[];
  error: string | null;
};

function useTickerData(symbol: string): TickerData {
  const [state, setState] = useState<TickerData>({ data: null, avgCost: null, transactions: [], error: null });
  useEffect(() => {
    let cancelled = false;
    fetchWithSchema(`${WORKER_BASE}/prices/${encodeURIComponent(symbol)}`, TickerPriceResponseSchema)
      .then(({ prices, transactions: txns }) => {
        if (cancelled) return;
        const buys = txns.filter((t) => t.actionType === "buy" || t.actionType === "reinvestment");
        const totalCost = buys.reduce((s, t) => s + Math.abs(t.amount), 0);
        const totalQty = buys.reduce((s, t) => s + Math.abs(t.quantity), 0);
        setState({ data: mergeTickerData(prices, txns), transactions: txns, avgCost: totalQty > 0 ? totalCost / totalQty : null, error: null });
      })
      .catch((e) => { if (!cancelled) setState((s) => ({ ...s, error: e instanceof Error ? e.message : "Failed to load" })); });
    return () => { cancelled = true; };
  }, [symbol]);
  return state;
}

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const { data, avgCost, transactions, error } = useTickerData(symbol);
  const [dialogOpen, setDialogOpen] = useState(false);

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

/**
 * Dialog-only entry: fetches ticker data and renders the full-screen
 * TickerChartDialog with no inline chart. Used when drilling into a
 * group's constituent from GroupChartDialog — caller controls lifetime
 * via the mounted symbol + onClose.
 */
export function TickerDialogOnly({ symbol, onClose }: { symbol: string; onClose: () => void }) {
  const { data, avgCost, transactions, error } = useTickerData(symbol);
  // Auto-dismiss if the constituent has no price history (e.g. a 401k pseudo-
  // ticker like "401k sp500" that isn't in /prices). Beats a stuck empty dialog.
  useEffect(() => {
    if (error || (data && data.length === 0)) onClose();
  }, [error, data, onClose]);
  if (!data || data.length === 0 || error) return null;
  return (
    <TickerChartDialog
      symbol={symbol}
      data={data}
      avgCost={avgCost}
      transactions={transactions}
      onClose={onClose}
    />
  );
}
