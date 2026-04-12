# Ticker Chart Integration — Buy/Sell Markers on Price History

## Status: IMPLEMENTED

Click a ticker in the Fidelity Activity table → expand an inline stock price chart with buy/sell markers overlaid.

## Implementation Summary

| Component | File | Status |
|-----------|------|--------|
| Price + transaction endpoint | `worker/src/index.ts` (`GET /prices/:symbol`) | Done |
| D1 sync: quantity, price columns | `pipeline/scripts/sync_to_d1.py` | Done |
| D1 sync: daily_close table | `pipeline/scripts/sync_to_d1.py` | Done |
| Zod schema: quantity, price | `src/lib/schema.ts` | Done |
| TickerChart component | `src/components/finance/ticker-chart.tsx` | Done |
| Expandable ticker rows | `src/components/finance/shared.tsx` (TickerTable/TickerRow) | Done |
| Worker URL from config | `src/lib/config.ts` (`WORKER_BASE`) | Done |

## Architecture

```
User clicks ticker row in Activity section
  → TickerRow expands
  → TickerChart fetches GET /prices/{symbol} from Worker
  → Worker queries daily_close + fidelity_transactions from D1
  → Chart renders: price line + buy markers (green) + sell markers (red) + avg cost reference line
```

Prices fetched on-demand per ticker — not bundled in `/timeline` (keeps main payload lean).

## Known Edge Cases

### Stock splits
`prices.py` fetches with `auto_adjust=True` (split-adjusted prices). Fidelity transaction quantities/prices are raw (unadjusted). No held tickers have split during holding period, so no visible impact yet. If a split occurs, historical net worth will show a discontinuity. Fix: switch to `auto_adjust=False` for price history, keep adjusted for market sparklines.

### Multiple accounts
D1 drops the `account` column. Same ticker in Taxable + Roth IRA shows all markers mixed together. Optional future enhancement: color-code by account.

### Non-trading days
`daily_close` only has trading day prices. Chart connects trading days with a line — no gap filling needed.
