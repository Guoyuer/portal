# Timemachine Data Pipeline TODO

Issues discovered during R2 vs timemachine.db reconciliation (April 2026).
Goal: timemachine backend becomes the single data source, fully replacing R2 `latest.json`.

---

## P0 — Data accuracy

### 1. Fix R2 pipeline Robinhood double-counting
**Files:** `pipeline/generate_asset_snapshot/config.py` (`manual_values_from_snapshot`)
**Problem:** Robinhood account is counted twice in the R2 pipeline:
- Fidelity CSV includes linked Robinhood positions (account `99eb806b`: ARM, UNH, ETH, cash)
- `manual_values_from_snapshot()` also adds Qianji's `Robinhood` balance because `Robinhood` is not in `fidelity_tracked`
- Impact: ~$2,323 overcounted in R2 total
**Fix:** Add `Robinhood` to `fidelity_tracked` so the Qianji balance is skipped.

### 2. Ingest Robinhood transaction history
**Problem:** Timemachine can't replay Robinhood positions — only has Qianji book value ($3,468 vs actual $3,373). No ticker-level breakdown for historical dates.
**Data source:** Robinhood web → Account → Reports & Statements → Generate Report (CSV)
**CSV columns:** `Activity Date, Process Date, Settle Date, Instrument, Description, Trans Code, Quantity, Price, Amount`
**Limitations:**
- Only ~1 year of history available — export ASAP before older data is lost
- Crypto (ETH) is NOT included in the standard CSV — only available via Tax Center or `robin_stocks` Python library
**Tasks:**
1. Export Robinhood CSV from web (stock transactions)
2. Write `robinhood_history.py` ingester (column mapping similar to Fidelity)
3. Add `ingest_robinhood_csv()` to `db.py`
4. Update `allocation.py` to include Robinhood replay
5. Handle ETH separately (manual entry or `robin_stocks`)
6. Update `build_timemachine_db.py` integration script

### 3. 401k proxy drift — get latest QFX
**Problem:** Latest QFX snapshot is 2024-06-30 — 21 months of proxy interpolation causes ~$3,000-4,000 category-level drift (US Equity vs Non-US Equity), though total impact is ~$1,075.
**Fix:** Download latest QFX from Empower and re-run `build_timemachine_db.py`. Should be done quarterly.

---

## P1 — Enrich timemachine data (enable all widgets)

### 4. Ticker-level allocation
**Problem:** `computed_daily` only stores 4 category-level aggregates. The allocation table needs subtype (broad/growth) and ticker-level breakdown.
**Where:** `allocation.py:144-185` already computes `ticker_values` per ticker, then discards it.
**Fix:** New table `computed_daily_tickers(date, ticker, value, category, subtype)`. New endpoint `GET /allocation?date=` returns ticker-level detail.

### 5. Lot-level cost basis tracking
**Problem:** No cost basis or gain/loss data for historical dates. Currently `replay()` only tracks `(account, symbol) → total_qty`.
**Where:** `fidelity_transactions` table already has every transaction's quantity + price + amount.
**Fix:** Enhance `replay()` to track `(account, symbol) → [(buy_date, qty, price), ...]`. Compute cost basis = sum(qty × price), gain/loss = market_value − cost_basis. Store in `computed_daily_tickers` as additional columns: `cost_basis, gain_loss, gain_loss_pct`.

### 6. Credit card / liabilities tracking
**Problem:** Timemachine only tracks assets. Balance sheet needs liabilities (credit cards).
**Where:** Qianji DB `user_asset` table has credit card accounts with negative balances. `replay_qianji()` currently skips them (`if usd_val <= 0: continue` in `allocation.py:163`).
**Fix:** Keep negative-balance accounts, store in new table `computed_daily_liabilities(date, account, balance)`. Backend returns `netWorth = total_assets + total_liabilities` (liabilities are negative).

### 7. Cashflow by category
**Problem:** `computed_prefix` only has total income/expenses. CashFlow widget needs per-category breakdown (e.g., Salary, Rent, Groceries).
**Where:** Qianji DB `user_bill` table has category info per transaction.
**Fix:** New prefix table `computed_prefix_categories(date, flow_type, category, cumulative_amount)`. Alternatively, query `user_bill` on demand for a date range — simpler but slower.

### 8. Activity by symbol
**Problem:** Portfolio Activity needs buysBySymbol, dividendsBySymbol. Prefix sums only have totals.
**Where:** `fidelity_transactions` table has all the raw data.
**Fix:** New endpoint `GET /activity?start=&end=` queries `fidelity_transactions` for the date range, groups by symbol. No new table needed.

---

## P2 — Market & live data (final R2 replacement)

### 9. Market context endpoint
**Problem:** Index returns, sparklines, macro indicators (Fed Rate, CPI, VIX, etc.) are not in timemachine DB.
**Nature:** This is live/current data, not historical — always shows "now" regardless of brush position.
**Fix:** New endpoint `GET /market` that fetches from yfinance + FRED on demand, with short TTL cache (5 min). Replaces the market section of `latest.json`.

### 10. Holdings detail endpoint
**Problem:** PE ratio, 52-week high/low, upcoming earnings — live market data per ticker.
**Fix:** New endpoint `GET /holdings-detail` that reads current positions from DB + fetches live data from yfinance. Cache with short TTL.

---

## Architecture: dropping R2

Once P0–P2 are complete, the architecture becomes:

```
timemachine.db (SQLite)
    ↓
FastAPI backend (server.py)
    ├── GET /timeline         — daily series + prefix sums (done)
    ├── GET /allocation?date= — ticker-level snapshot (P1)
    ├── GET /cashflow?start=&end= — per-category flows (P1)
    ├── GET /activity?start=&end= — buys/divs by symbol (P1)
    ├── GET /market            — live index/macro data (P2)
    └── GET /holdings-detail   — live per-ticker detail (P2)
    ↓
Next.js frontend (Cloudflare Pages, static)
    - useTimeline() hook drives brush → all widgets react
    - No more REPORT_URL / R2 fetch
```

**Deployment:** Backend on Fly.io free tier (or similar). DB rebuilt weekly via GitHub Actions, uploaded to the server.

**R2 removal:** After all endpoints are live and verified, delete `REPORT_URL`, `NEXT_PUBLIC_R2_URL`, and the R2 report generation pipeline.

---

## Implementation order

```
P0 (data accuracy):     #1 (30 min), #2 (2-3 hours), #3 (15 min)
P1 (enrich data):       #4 (1-2 hours), #5 (2-3 hours), #6 (1 hour), #7 (2-3 hours), #8 (1-2 hours)
P2 (live data):         #9 (2-3 hours), #10 (2-3 hours)
Frontend integration:   Wire all widgets to useTimeline + new endpoints (~1-2 days)
Final:                  Verify parity → remove R2 pipeline
```
