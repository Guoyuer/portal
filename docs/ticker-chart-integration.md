# Ticker Chart Integration — Buy/Sell Markers on Price History

## Goal

Click a ticker in the Fidelity Activity table → expand an inline stock price chart with buy/sell markers overlaid. One glance shows whether your trade timing was good.

## Current State

### Already have
- **Fidelity transactions** with full detail: symbol, date, quantity, price, amount, actionType (pipeline `types.py:98-110`, db `db.py:17-32`)
- **Historical price cache**: yfinance daily close prices stored in `daily_close` table, fetched per symbol's holding period (`prices.py`)
- **Frontend activity summary**: buysBySymbol / sellsBySymbol / dividendsBySymbol aggregated in `compute.ts:199-230`
- **Recharts** already in the project — no new chart library needed

### Gap
| Data | Pipeline | D1 | Frontend | Status |
|------|----------|-----|----------|--------|
| symbol | ✅ | ✅ | ✅ | OK |
| date (runDate) | ✅ | ✅ | ✅ | OK |
| amount | ✅ | ✅ | ✅ | OK |
| actionType | ✅ | ✅ | ✅ | OK |
| **quantity** | ✅ | ❌ stripped | ❌ | **BLOCKER** |
| **price** | ✅ | ❌ stripped | ❌ | **BLOCKER** |
| **daily close prices** | ✅ (daily_close table) | ❌ not synced | ❌ | **BLOCKER** |

The `sync_to_d1.py:44-47` intentionally limits fidelity_transactions to `["run_date", "action_type", "symbol", "amount"]`. Quantity and price exist in timemachine.db but never reach the frontend.

Daily close prices exist in the pipeline's SQLite but are not synced to D1 at all.

## Integration Plan

### UX

In "Fidelity Activity" section, the Buys/Sells by Symbol tables already list tickers. Clicking a ticker row expands an inline chart (same pattern as Net Worth → Allocation expand). The chart shows:

- **Line**: daily close price over the holding period
- **Green dots**: buy transactions, positioned at (date, price)
- **Red dots**: sell transactions, positioned at (date, price)
- **Hover tooltip**: date, action, quantity, price, total amount
- **52-week range bar** below (already computed in `precompute.py`)

No new page. No new route. Just an expandable row.

### Data Pipeline Changes

#### 1. Restore quantity + price to D1

**`pipeline/scripts/sync_to_d1.py`** — add columns to sync:
```python
_D1_COLUMNS = {
    "fidelity_transactions": ["run_date", "action_type", "symbol", "amount", "quantity", "price"],
}
```

**`worker/schema.sql`** — add columns to D1 table:
```sql
CREATE TABLE fidelity_transactions (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date        TEXT NOT NULL,
  action_type     TEXT NOT NULL DEFAULT '',
  symbol          TEXT NOT NULL DEFAULT '',
  amount          REAL NOT NULL DEFAULT 0,
  quantity        REAL NOT NULL DEFAULT 0,   -- NEW
  price           REAL NOT NULL DEFAULT 0    -- NEW
);
```

**`pipeline/scripts/gen_schema_sql.py`** — update if auto-generated.

#### 2. Sync daily close prices to D1

**`pipeline/generate_asset_snapshot/db.py`** — `daily_close` table already exists in timemachine.db:
```sql
CREATE TABLE daily_close (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    close  REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);
```

**`pipeline/scripts/sync_to_d1.py`** — add `daily_close` to sync targets:
```python
_D1_COLUMNS = {
    ...
    "daily_close": ["symbol", "date", "close"],
}
```

**`worker/schema.sql`** — add table:
```sql
CREATE TABLE daily_close (
  symbol TEXT NOT NULL,
  date   TEXT NOT NULL,
  close  REAL NOT NULL,
  PRIMARY KEY (symbol, date)
);
```

#### 3. Worker API

Two options:

**Option A — Extend `/timeline` response** (adds to existing single-fetch):
Add `dailyClose` to the timeline payload. Downside: adds ~200KB+ for all symbols' price history to an already large response.

**Option B — New `/prices/:symbol` endpoint** (recommended):
```typescript
// GET /prices/VTI?start=2024-01-01&end=2026-04-10
// Returns: { prices: [{ date: "2024-01-01", close: 234.56 }, ...] }
```

Fetched on-demand only when user clicks a ticker. Keeps `/timeline` lean.

### Frontend Changes

#### 4. Update TypeScript types

**`src/lib/schema.ts`**:
```typescript
const FidelityTxnSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  symbol: z.string(),
  amount: z.number(),
  quantity: z.number(),  // NEW
  price: z.number(),     // NEW
});
```

#### 5. New TickerChart component

**`src/components/finance/ticker-chart.tsx`**:

Uses Recharts (already in project). Structure:

```
<ResponsiveContainer>
  <ComposedChart>
    <Line dataKey="close" />           // price history line
    <Scatter dataKey="buyPrice" />     // green buy markers
    <Scatter dataKey="sellPrice" />    // red sell markers
    <Tooltip />                        // date, price, qty, amount
    <ReferenceLine y={avgCost} />      // average cost basis
  </ComposedChart>
</ResponsiveContainer>
```

Data merging logic (similar to TradeTrack's `dataProcessing.ts`):
- Join daily_close prices with fidelity_transactions by (symbol, date)
- Each data point: `{ date, close, buyPrice?, sellPrice?, qty?, amount? }`

#### 6. Integrate into PortfolioActivity

**`src/components/finance/portfolio-activity.tsx`**:

Make ticker rows in TickerTable clickable. On click, expand to show `<TickerChart symbol={symbol} />`. The chart fetches prices on-demand via the new `/prices/:symbol` endpoint.

### What We Don't Need

- ❌ User login/registration (data comes from Fidelity CSV pipeline)
- ❌ ECharts (use existing Recharts)
- ❌ Redux (portal uses useBundle single-fetch pattern)
- ❌ Real-time price API (pipeline caches daily close via yfinance)
- ❌ Manual trade entry (all from Fidelity export)

## Data Edge Cases

### 1. Stock splits — CRITICAL (existing bug, not just ticker chart)

`prices.py` fetches with `auto_adjust=True`, so all historical prices are split-adjusted. But Fidelity transaction quantities and prices are **raw** (not adjusted).

**This affects the existing timemachine net worth calculation**, not just the future ticker chart. In `allocation.py:164-169`, portfolio value = `qty * price`. If a ticker splits:
- Pre-split dates: qty is pre-split (e.g. 10 shares), price is split-adjusted (e.g. $100 instead of $400) → value = $1,000 instead of $4,000
- Post-split dates: qty is post-split (40 shares), price is correct → value = $4,000 ✓

Currently no held tickers (VTI, VOO, VXUS, etc.) have split during the holding period, so no visible impact yet. But any future split will cause a discontinuity in the historical net worth curve. A previous occurrence was fixed ad hoc by manually patching the database, but the code-level bug remains — it will resurface on next `build_timemachine_db.py` rebuild or new split event.

**Fix**: Switch to `auto_adjust=False` (unadjusted prices). This keeps prices consistent with raw transaction records. For the chart, unadjusted prices are actually more intuitive — they show what you actually paid vs what the stock actually traded at. For returns/sparklines in market context, continue using adjusted prices (separate fetch).

### 2. Price history start date — no pre-buy context

`symbol_holding_periods()` starts from `first_buy_date`. The chart needs price context before the first buy to show what the price was doing before you entered.

**Fix**: Extend `fetch_and_store_prices` to fetch from `first_buy_date - 90 days` (or configurable lookback).

### 3. Date format mismatch

- `fidelity_transactions.run_date` = "MM/DD/YYYY"
- `daily_close.date` = "YYYY-MM-DD"

Frontend joining these by date will break unless one is normalized.

**Fix**: Normalize in the frontend merge function, or store run_date as ISO format during sync.

### 4. Reinvestments double-counted

`compute.ts:218-220` counts reinvestments as both buys and dividends. On the chart, reinvestments should show as buy markers only (they are automatic buys at market price).

**Fix**: In the chart component, filter `actionType === "reinvestment"` as buys, not dividends.

### 5. Multiple accounts

D1 sync drops the `account` column. Same ticker bought in both Taxable and Roth IRA shows all markers mixed together with no account attribution.

**Fix (optional)**: Add `account` column to D1 sync. Could color-code markers by account, or add a filter dropdown.

### 6. Non-trading days

`daily_close` only has trading day prices. Weekends/holidays have no rows. This is correct for the chart — just connect trading days with a line, no need to fill gaps.

## Data Size Estimate

- `daily_close`: ~250 rows per symbol per year × ~15 symbols ≈ 10K rows total, ~200KB
- `quantity` + `price` columns: negligible (already syncing the row)

## File Checklist

| File | Change |
|------|--------|
| `pipeline/scripts/sync_to_d1.py` | Add quantity, price columns; add daily_close table |
| `worker/schema.sql` | Add columns + new table |
| `worker/src/index.ts` | New `/prices/:symbol` endpoint |
| `src/lib/schema.ts` | Add quantity, price to FidelityTxn |
| `src/components/finance/ticker-chart.tsx` | New component |
| `src/components/finance/portfolio-activity.tsx` | Make rows expandable |
