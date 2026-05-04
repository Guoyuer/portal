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
import { TickerPricesBundleSchema, type TickerPricesBundle, type TickerTxn } from "@/lib/schemas/ticker";
import { PRICES_URL } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";
import { mergeTickerData, computeAvgCost, type TickerChartPoint } from "@/lib/data/ticker-data";
import { TickerChartBase } from "./ticker-chart-base";
import { TickerChartDialog } from "./ticker-dialog";

type TickerDataBase = {
  symbol: string;
  avgCost: number | null;
  transactions: TickerTxn[];
};

type TickerDataState =
  | (TickerDataBase & { status: "pseudo"; data: null; error: null })
  | (TickerDataBase & { status: "loading"; data: null; error: null })
  | (TickerDataBase & { status: "missing"; data: []; error: null })
  | (TickerDataBase & { status: "empty"; data: []; error: null })
  | (TickerDataBase & { status: "error"; data: null; error: string })
  | (TickerDataBase & { status: "data"; data: TickerChartPoint[]; error: null });

function emptyState(status: "pseudo" | "loading", symbol: string): TickerDataState {
  return { status, symbol, data: null, avgCost: null, transactions: [], error: null };
}

function noDataState(
  status: "missing" | "empty",
  symbol: string,
  transactions: TickerTxn[] = [],
  avgCost: number | null = null,
): TickerDataState {
  return { status, symbol, data: [], transactions, avgCost, error: null };
}

const CLOSE_DIALOG_STATUSES = new Set<TickerDataState["status"]>(["pseudo", "missing", "empty", "error"]);

let pricesBundlePromise: Promise<TickerPricesBundle> | null = null;

function loadPricesBundle(): Promise<TickerPricesBundle> {
  pricesBundlePromise ??= fetchWithSchema(PRICES_URL, TickerPricesBundleSchema)
    .catch((err: unknown) => {
      pricesBundlePromise = null;
      throw err;
    });
  return pricesBundlePromise;
}

export function useTickerData(symbol: string): TickerDataState {
  const stateSymbol = IS_PSEUDO_TICKER(symbol) ? symbol : symbol.toUpperCase();
  const [state, setState] = useState<TickerDataState>(() => (
    emptyState(IS_PSEUDO_TICKER(symbol) ? "pseudo" : "loading", stateSymbol)
  ));
  useEffect(() => {
    if (IS_PSEUDO_TICKER(symbol)) return;
    const canonical = symbol.toUpperCase();
    let cancelled = false;
    loadPricesBundle()
      .then((bundle) => {
        if (cancelled) return;
        const ticker = bundle[canonical];
        if (!ticker) {
          setState(noDataState("missing", canonical));
          return;
        }
        const data = mergeTickerData(ticker.prices, ticker.transactions);
        const avgCost = computeAvgCost(ticker.transactions);
        if (data.length === 0) {
          setState(noDataState("empty", canonical, ticker.transactions, avgCost));
          return;
        }
        setState({ status: "data", symbol: canonical, data, transactions: ticker.transactions, avgCost, error: null });
      })
      .catch((e) => {
        if (!cancelled) {
          setState({
            status: "error",
            symbol: canonical,
            data: null,
            avgCost: null,
            transactions: [],
            error: e instanceof Error ? e.message : "Failed to load",
          });
        }
      });
    return () => { cancelled = true; };
  }, [symbol]);
  if (state.symbol !== stateSymbol) {
    return emptyState(IS_PSEUDO_TICKER(symbol) ? "pseudo" : "loading", stateSymbol);
  }
  return state;
}

// 401k pseudo-tickers ("401k sp500", "401k tech", "401k ex-us") come from
// the Empower QFX source and don't exist in the prices bundle — they represent the
// user's holding within the 401k plan, not a tradable market symbol.
const IS_PSEUDO_TICKER = (sym: string) => sym.startsWith("401k ");

export function TickerChart({ symbol, startDate, endDate }: { symbol: string; startDate?: string; endDate?: string }) {
  const tickerData = useTickerData(symbol);
  const [dialogOpen, setDialogOpen] = useState(false);

  if (tickerData.status === "pseudo") {
    return <p className="text-xs text-muted-foreground py-2">No price chart for 401k pseudo-tickers</p>;
  }
  if (tickerData.status === "error") {
    return <p className="text-xs text-red-400 py-2">Failed to load chart: {tickerData.error}</p>;
  }
  if (tickerData.status === "loading") {
    return <p className="text-xs text-muted-foreground py-2 animate-pulse">Loading {symbol} chart...</p>;
  }
  if (tickerData.status === "empty" || tickerData.status === "missing") {
    const isMM = /^(SPAXX|FDRXX|FZFXX|FCASH)$/.test(symbol);
    const msg = isMM ? "Money market fund \u2014 price fixed at $1.00" : `No price data for ${symbol}`;
    return <p className="text-xs text-muted-foreground py-2">{msg}</p>;
  }

  // Filter to the global brush range if provided
  const filtered = (startDate && endDate)
    ? tickerData.data.filter((p) => p.date >= startDate && p.date <= endDate)
    : tickerData.data;

  if (filtered.length === 0) {
    return <p className="text-xs text-muted-foreground py-2">No price data for {symbol} in selected range</p>;
  }

  return (
    <div
      className="cursor-zoom-in relative group"
      onClick={(e) => { e.stopPropagation(); setDialogOpen(true); }}
    >
      <TickerChartBase data={filtered} avgCost={tickerData.avgCost} />
      <div className="absolute top-1 right-1 text-xs text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        &#x26F6;
      </div>
      {dialogOpen && (
        <TickerChartDialog
          symbol={symbol}
          data={filtered}
          avgCost={tickerData.avgCost}
          transactions={tickerData.transactions}
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
  const tickerData = useTickerData(symbol);
  // Auto-dismiss if the constituent has no price history (e.g. a 401k pseudo-
  // ticker like "401k sp500" that isn't in /prices). Beats a stuck empty dialog.
  useEffect(() => {
    if (CLOSE_DIALOG_STATUSES.has(tickerData.status)) onClose();
  }, [tickerData.status, onClose]);
  if (tickerData.status !== "data") return null;
  return (
    <TickerChartDialog
      symbol={symbol}
      data={tickerData.data}
      avgCost={tickerData.avgCost}
      transactions={tickerData.transactions}
      onClose={onClose}
    />
  );
}
