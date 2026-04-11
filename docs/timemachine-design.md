# Timemachine Design

Global allocation history: travel to any date, see the full portfolio picture.

## What it does

A time axis with a Recharts brush/traveller. User drags to navigate. Two data modes:

- **Point-in-time** (right edge of brush): allocation, positions, cash balances at that date
- **Time-range** (brush selection): cash flow, activity summary, transactions in that window

## Architecture

```mermaid
graph TB
    subgraph Sources["Data Sources"]
        FID[Fidelity CSVs]
        QFX[Empower 401k QFX]
        QJ[(Qianji DB)]
        YF[Yahoo Finance]
    end

    subgraph Ingest["Ingestion"]
        I_FID[Fidelity Ingest]
        I_QFX[401k QFX Parser]
        I_QJ[Qianji Reader]
        I_PRICE[Price Fetcher]
    end

    subgraph DB["SQLite - timemachine.db"]
        T_TXN[(fidelity_transactions)]
        T_401[(empower_snapshots)]
        T_PRICE[(daily_close + CNY rates)]
        T_QJ[(qianji_records)]
    end

    subgraph Replay["Replay Engine"]
        R_FID[Fidelity Replay]
        R_401[401k Interpolation]
        R_QJ[Qianji Replay]
        MERGE[Merge + Categorize]
    end

    subgraph Precompute["Pre-computation"]
        PC_DAILY["computed_daily — point-in-time values"]
        PC_TICKERS["computed_daily_tickers — per-ticker detail"]
        PC_MARKET["computed_market_* — indices + indicators"]
    end

    subgraph Cloud["Cloudflare D1 + Worker"]
        D1[(D1 portal-db)]
        WORKER["Worker GET /timeline<br/>7 parallel SELECTs → JSON"]
    end

    subgraph FE["Next.js Frontend"]
        FETCH["fetch /timeline (once on load)"]
        COMPUTE["compute.ts — pure functions:<br/>allocation · cashflow · activity · cross-check"]
        BRUSH["Brush + Traveller"]
    end

    FID --> I_FID --> T_TXN
    QFX --> I_QFX --> T_401
    QJ --> I_QJ --> T_QJ
    YF --> I_PRICE --> T_PRICE

    T_TXN --> R_FID
    T_401 --> R_401
    T_PRICE --> R_401
    T_PRICE --> MERGE
    T_QJ --> R_QJ

    R_FID --> MERGE
    R_401 --> MERGE
    R_QJ --> MERGE

    MERGE --> PC_DAILY
    MERGE --> PC_TICKERS
    MERGE --> PC_MARKET

    PC_DAILY -->|sync_to_d1.py| D1
    PC_TICKERS -->|sync_to_d1.py| D1
    PC_MARKET -->|sync_to_d1.py| D1
    D1 --> WORKER
    WORKER -->|JSON ~385KB gzip| FETCH
    FETCH --> COMPUTE
    COMPUTE --> BRUSH
```

## Frontend performance model

All computation happens client-side after a single fetch. Zero network during brush interaction:

| Tier | When | Data | Cost |
|------|------|------|------|
| **Initial load** | Page open | `GET /timeline` → all data (~4.6 MB JSON, ~385 KB gzip) | One request |
| **During drag** | Every frame | `daily[rightEdge]` for point-in-time, iterate txns for range | O(n) but instant (<1ms for 5yr data) |

All daily data points are rendered directly (no downsampling). `compute.ts` contains pure functions for allocation, cashflow, activity, and cross-check — called on every brush position change with no network round-trips.

### daily[] — point-in-time (drives chart + summary tiles)

One row per trading day (~800 rows for 3 years):

```typescript
{
  date: string        // "2025-06-15"
  total: number       // net worth
  usEquity: number    // category values
  nonUsEquity: number
  crypto: number
  safeNet: number
  liabilities: number // credit cards (negative)
}
```

### Raw transactions — drives range computation

Frontend receives raw `fidelityTxns[]` and `qianjiTxns[]`. `compute.ts` iterates them to compute cashflow (income/expenses/savings rate) and activity (buys/sells/dividends) for any date range. No pre-aggregated prefix sums needed.

### Allocation detail — per-ticker breakdown

Computed client-side from `dailyTickers[]` index:

```typescript
{
  total: number
  netWorth: number
  liabilities: number
  categories: { name, value, pct, target, deviation }[]
  tickers: { ticker, value, category, subtype, costBasis, gainLoss, gainLossPct }[]
}
```

## Data sources

| Source | What it provides | Historical method |
|--------|-----------------|-------------------|
| Fidelity transactions | Positions (shares per symbol per account) | Replay — verified 36/36 positions, 3/3 cash |
| Empower 401k QFX | 401k per-fund positions | Quarterly snapshots + proxy daily interpolation + contribution compensation |
| Yahoo Finance | Daily close prices + USD/CNY rate | Holding-period scoped, cached in SQLite |
| Qianji SQLite | Non-investment account balances | Reverse-replay from current balances. `user_bill.time` is the **user-specified transaction date** (not the bookkeeping timestamp); users can back-date entries in the app. |
| Config | Category/subtype/weight mapping | Static, same as today |

## How timemachine rebuilds allocation at any date

```
Fidelity replay(as_of) → positions + cash
401k interpolation(as_of) → per-fund values via proxy returns
Qianji replay(as_of) → account balances (native currency)
                ↓
    Merge + Categorize (config)
    positions × daily_close prices
    Qianji balances / historical CNY rate
    401k values by fund ticker
                ↓
    {ticker → value} → {category → value, pct, target, deviation}
```

### Fidelity replay (verified)

Module: `pipeline/generate_asset_snapshot/timemachine.py`

- Action prefixes: `YOU BOUGHT`, `YOU SOLD`, `REINVESTMENT`, `REDEMPTION PAYOUT`, `TRANSFERRED FROM`, `TRANSFERRED TO`, `DISTRIBUTION`, `EXCHANGED TO`
- `holdings[(account, symbol)] += quantity` — qty sign encodes direction
- Ignore Cash/Margin lot type, aggregate by (account, symbol)
- Cash: `sum(Amount where Type != "Shares") + sum(MM REINVESTMENT Quantity)`
- Accounts with replay: Z29133576 (Taxable), 238986483 (Roth), Z29276228 (Cash Mgmt)

### 401k (Empower QFX)

Module: `pipeline/generate_asset_snapshot/empower_401k.py`

- 12 quarterly QFX snapshots (2023-Q1 → 2025-Q4), exact per-fund mktval
- Between snapshots: `value(date) = snapshot_mktval × (proxy_today / proxy_at_snapshot)`
- Post-snapshot contributions: split 50/50 sp500/ex-us, scaled by proxy returns
- Fund → proxy: S&P 500 → VOO, Harbor Capital → QQQM, ex-US → VXUS
- Auto-corrects when new QFX is added

### Qianji replay (verified)

Reverse-replay from current `user_asset` balances:
- **Date semantics:** `user_bill.time` is the user-specified transaction date (Unix seconds, UTC), not the bookkeeping/creation timestamp. The replay cutoff compares against this date, so balances reflect when transactions *occurred* per the user, not when they were recorded.
- Expense: undo by adding back to `fromact`
- Income: undo by subtracting from `fromact`
- Transfer/Repayment: undo both sides (cross-currency via `extra.curr.tv`)
- Skip: accounts covered by Fidelity replay + "401k" (covered by QFX)
- CNY conversion: historical USD/CNY rate from Yahoo Finance

### Historical prices

- Holding-period scoped: only fetch prices for dates when symbol was held
- Cached in `prices.db` (SQLite), incremental updates
- Forward-fill for mutual funds (priced T-1) and weekends
- ~16K records for 66 symbols over 3 years

## Verification results

| Check | Result |
|-------|--------|
| Fidelity positions @ Apr-07-2026 | 36/36 exact match |
| Fidelity cash @ Apr-07-2026 | 3/3 exact match |
| Fidelity positions @ Aug-25-2025 | 22/22 exact match |
| Fidelity positions @ Apr-03-2026 | 34/36 (2 differ by post-snapshot reinvestment) |
| 401k at all 12 QFX quarter boundaries | 12/12 zero error |
| Allocation vs live site | Total: -1.2%, each category < 1.5pp |
| Safe Net % | 25.7% computed vs 26.5% live (0.8pp, from 401k sub-fund approximation) |

## Architecture evolution

### Legacy (removed): static pipeline via R2
```
Python pipeline → latest.json → R2 → browser (daily batch, single snapshot)
```
R2 pipeline, `report.py`, `json_renderer.py`, `send_report.py`, `sync.py`, and `report.yml` workflow have all been deleted. Only `/econ` page still reads from R2 (`econ.json`).

### Current: D1 + Workers (fully deployed)
```
Data sources → Ingestion → SQLite (timemachine.db) → Replay → Pre-compute
  → sync_to_d1.py → D1 + Worker → Next.js (static shell)
```
Worker is pure passthrough: 7 parallel SELECTs → JSON. Frontend computes everything locally. Same Worker code runs locally via `wrangler dev --remote` and in production.
