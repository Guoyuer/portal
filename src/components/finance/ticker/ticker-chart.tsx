"use client";

// ── Ticker chart public entry ────────────────────────────────────────────
//
// Composes:
// - Pure data layer: @/lib/data/ticker-data (mergeTickerData, clusterByTime, ...)
// - Inline chart: ./ticker-chart-base  (TickerChartBase, non-interactive)
// - Dialog: ./ticker-dialog         (near-fullscreen modal with clustering)
//
// This file is kept small deliberately — it owns only the lazy /prices bundle
// fetch, per-symbol lookup, range-filtering, and the expand-to-dialog gesture.

import { useEffect, useState } from "react";
import { TickerPricesBundleSchema, type TickerPricesBundle, type TickerTxn } from "@/lib/schemas";
import { PRICES_URL } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";
import { mergeTickerData, computeAvgCost, type TickerChartPoint } from "@/lib/data/ticker-data";
import { TickerChartBase } from "./ticker-chart-base";
import { TickerChartDialog } from "./ticker-dialog";

type TickerData = {
  data: TickerChartPoint[] | null;
  avgCost: number | null;
  transactions: TickerTxn[];
  error: string | null;
};

let pricesBundlePromise: Promise<TickerPricesBundle> | null = null;

function loadPricesBundle(): Promise<TickerPricesBundle> {
  pricesBundlePromise ??= fetchWithSchema(PRICES_URL, TickerPricesBundleSchema)
    .catch((err: unknown) => {
      pricesBundlePromise = null;
      throw err;
    });
  return pricesBundlePromise;
}

export function useTickerData(symbol: string): TickerData {
  const [state, setState] = useState<TickerData>({ data: null, avgCost: null, transactions: [], error: null });
  useEffect(() => {
    if (IS_PSEUDO_TICKER(symbol)) return;
    const canonical = symbol.toUpperCase();
    let cancelled = false;
    loadPricesBundle()
      .then((bundle) => {
        if (cancelled) return;
        const { prices, transactions: txns } = bundle[canonical] ?? { symbol: canonical, prices: [], transactions: [] };
        setState({ data: mergeTickerData(prices, txns), transactions: txns, avgCost: computeAvgCost(txns), error: null });
      })
      .catch((e) => { if (!cancelled) setState((s) => ({ ...s, error: e instanceof Error ? e.message : "Failed to load" })); });
    return () => { cancelled = true; };
  }, [symbol]);
  return state;
}

// 401k pseudo-tickers ("401k sp500", "401k tech", "401k ex-us") come from
// the Empower QFX source and don't exist in the prices bundle — they represent the
// user's holding within the 401k plan, not a tradable market symbol.
const IS_PSEUDO_TICKER = (sym: string) => sym.startsWith("401k ");

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const { data, avgCost, transactions, error } = useTickerData(symbol);
  const [dialogOpen, setDialogOpen] = useState(false);

  if (IS_PSEUDO_TICKER(symbol)) {
    return <p className="text-xs text-muted-foreground py-2">No price chart for 401k pseudo-tickers</p>;
  }
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
