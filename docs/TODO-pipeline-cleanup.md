# Pipeline Cleanup TODO

Notes from April 9, 2026 session. Covers data pipeline simplification, bug fixes, and automation.

---

## Background

### Qianji date semantics
`user_bill.time` is the **user-specified transaction date** (Unix seconds, UTC), not the bookkeeping/creation timestamp. Users can back-date or forward-date entries in the Qianji app. The replay cutoff compares against this field, so balances reflect when transactions *occurred* per the user, not when they were recorded.

### How net worth / portfolio total is computed
`computed_daily.total` is the sum of all positive-value tickers on a given date, assembled from four sources in `allocation.py`:

| Source | How value is derived |
|--------|---------------------|
| Fidelity positions | Forward replay of transaction CSV → `(account, symbol) → qty` × `daily_close` price |
| Fidelity cash | Forward replay → per-account cash balance, mapped to FZFXX |
| Qianji accounts | Reverse replay from current `user_asset` balances, CNY converted at historical rate |
| Empower 401k | QFX quarterly snapshots + proxy daily interpolation + Qianji contribution fallback |
| Robinhood | Forward replay of Robinhood CSV → `symbol → qty` × `daily_close` price |

`netWorth = total + liabilities` (liabilities are negative, i.e., credit cards from Qianji).

### D1 synced tables (7 tables)

| Table | Purpose | Used by |
|-------|---------|---------|
| `computed_daily` | Per-trading-day totals + 4 categories + liabilities | Chart, snapshot tiles |
| `computed_prefix` | Cumulative prefix sums (income, expenses, buys, ...) | Range tiles, monthly flow chart |
| `computed_daily_tickers` | Per-day per-ticker value, category, cost basis | Allocation table |
| `fidelity_transactions` | Raw Fidelity transaction records | Frontend: activity by symbol, cross-check |
| `qianji_transactions` | Raw Qianji cashflow records | Frontend: cashflow by category, cross-check |
| `computed_market` | Market indices (S&P, NASDAQ, CSI 300) + FRED indicators | Market context section |
| `computed_holdings_detail` | Per-ticker month return, 52w high/low | Holdings detail section |

---

## P0 — Bug fixes (R2 vs D1 logic differences)

### 1. 401K category detection is case-sensitive
**File:** `src/lib/use-bundle.ts:91`
**Problem:** `i.category === "401K"` is an exact match. R2 pipeline used `"401" in cat.lower()` (case-insensitive substring). If Qianji category is `"401k"` or `"401K Pre-tax"`, the D1 path will miss it, causing `takehomeSavingsRate` to be wrong (won't deduct 401K from take-home income).
**Fix:** `i.category.toLowerCase().includes("401")`

### 2. Sell amount sign inconsistency
**File:** `pipeline/generate_asset_snapshot/precompute.py:59` vs `src/lib/use-bundle.ts:201`
**Problem:** `precompute.py` uses raw amount for sells (`bucket["sells"] += amount`), frontend uses `Math.abs(t.amount)`. If Fidelity sell amounts are negative, prefix sums will show negative sells while frontend activity shows positive.
**Fix:** Use `abs(amount)` in `precompute.py` line 59, consistent with buys on line 57.

### 3. Reinvestment double-counting (intentional change, document it)
**D1 path** counts each reinvestment in both `buys_by_symbol` and `dividends_by_symbol` (use-bundle.ts:208-217, precompute.py:62-64). **R2 path** tracked `reinvestments_total` separately (report.py:191-192). The D1 approach is more accurate (reinvestment = dividend received + auto-buy), but makes buys/dividends totals larger than R2 reports. This is intentional — no fix needed, but good to be aware of when comparing.

---

## P1 — Remove `computed_prefix` table

**Rationale:** The frontend already iterates raw `qianji_transactions` and `fidelity_transactions` to compute per-category cashflow and per-symbol activity. For ~4,000 transactions (5 years), this takes < 1ms. The prefix table provides O(1) range totals, but those exact numbers are already computed as byproducts of the category/symbol aggregation. The prefix table adds pipeline complexity and has caused the sell-sign inconsistency (P0 #2).

**Current consumers:**
- `timemachine.tsx:171-177` — shows Income/Expenses/Buys/Dividends in brush panel → replace with `cashflow.totalIncome`, `cashflow.totalExpenses`, `activity.buysBySymbol` sum, `activity.dividendsBySymbol` sum
- `finance/page.tsx:90` — `computeMonthlyFlows()` uses prefix array for monthly bar chart → rewrite to aggregate `qianji_transactions` by month
- `finance/page.tsx:83` — `tl.range?.buys` for "invested" metric → replace with sum of `activity.buysBySymbol`

**Changes:**
- Delete `computed_prefix` from `db.py`, `precompute.py`, `build_timemachine_db.py`
- Delete from `sync_to_d1.py` TABLES_TO_SYNC (7→6)
- Delete `v_prefix` view from `worker/schema.sql`
- Remove `prefix` field from Worker `/timeline` response
- Remove `PrefixPointSchema` from `schema.ts`
- Rewrite `computeMonthlyFlows()` in `finance/page.tsx`
- Update `timemachine.tsx` brush panel to read from cashflow/activity

---

## P2 — Remove R2 legacy path

**What to delete:**
- `.github/workflows/report.yml` — daily R2 report generation
- `pipeline/scripts/sync.py` — raw file upload to R2
- `pipeline/scripts/send_report.py` — latest.json generation
- `pipeline/generate_asset_snapshot/report.py` — R2 report builder
- `pipeline/generate_asset_snapshot/renderers/json_renderer.py` — camelCase serializer for R2
- `NEXT_PUBLIC_R2_URL` from `.env.local`, CI secrets, `config.ts`
- `REPORT_URL`, `ECON_URL` from `src/lib/config.ts`

**What to migrate first:**
- `/econ` page currently fetches `econ.json` from R2. Move FRED time-series data into D1 (new table or expand `computed_market`) and serve through the Worker.

---

## P3 — Automate D1 pipeline (remove manual steps)

**Current flow (manual):**
```
Mac launchd → sync.py → R2        (automated, but R2-only)
Local:  build_timemachine_db.py    (manual, hardcoded Windows paths)
Local:  sync_to_d1.py              (manual)
```

**Target flow (fully automated):**
```
Mac launchd → run.sh:
  1. Detect changes (Qianji DB mtime, new CSVs in Downloads)
  2. build_timemachine_db.py --incremental
  3. sync_to_d1.py
  → D1 updated, Worker serves fresh data (1hr CDN cache)
```

**Changes needed:**
- `build_timemachine_db.py`: parameterize paths via `--data-dir` / env var, remove hardcoded `C:/Users/guoyu/...`
- New `pipeline/scripts/run.sh`: detect changes → build → sync, single entry point
- New launchd plist / Windows Task Scheduler task to run `run.sh` daily
- CI (`ci.yml`) stays code-only: test → Pages + Worker deploy

---

## P4 — Replay checkpoint caching + positions verification

### Problem
Even in `--incremental` mode, every new date requires a full forward replay — traversing all historical transactions from the very first one up to that date. `allocation.py:122-127` caches positions between consecutive days without transactions, but each replay still reads the entire CSV from the start. This gets slower as transaction history grows.

Reverse replay (from a positions CSV snapshot) was considered but rejected: it requires a positions CSV as anchor, and cost basis tracking is not cleanly invertible (division-by-zero on full sells, precision loss on partial sells). Since we don't want to depend on a positions CSV for normal operation, forward replay remains the only way to reconstruct holdings from pure transaction history.

### Proposed: checkpoint caching
Cache the forward replay state (positions + cash + cost_basis) at periodic dates in the DB. Incremental builds resume from the latest checkpoint instead of replaying from scratch.

**New table in `timemachine.db`:**
```sql
CREATE TABLE replay_checkpoint (
    date       TEXT PRIMARY KEY,
    positions  TEXT NOT NULL,  -- JSON: {"(acct,sym)": qty, ...}
    cash       TEXT NOT NULL,  -- JSON: {"acct": balance, ...}
    cost_basis TEXT NOT NULL   -- JSON: {"(acct,sym)": basis, ...}
);
```

**Incremental replay flow:**
```
Without checkpoint:  txn[0] → txn[1] → ... → txn[2000] → today     (all from scratch)
With checkpoint:     [checkpoint @ txn 1990] → txn[1991..2000] → today  (10 txns only)
```

**Checkpoint strategy:**
- After a full build, save a checkpoint at the latest date
- Incremental build: load latest checkpoint, replay only transactions after that date
- Optionally save a new checkpoint after each incremental build

### Positions CSV calibration (--positions)
When a `Portfolio_Positions_*.csv` is available, use it to **calibrate** replay state — not just verify. Fidelity's positions CSV includes `Cost Basis Total` per holding (computed using the user's actual lot selection method, e.g. specific lot identification). The current replay uses average cost, which diverges from Fidelity's numbers when specific lots are sold.

`portfolio.py:29` already reads `Cost Basis Total` from this CSV (used by the R2 legacy path). Reuse that parsing for calibration.

```bash
# Normal build (no CSV needed, uses replay + average cost)
python scripts/build_timemachine_db.py --incremental

# Calibrate with positions export (optional, when available)
python scripts/build_timemachine_db.py --incremental --positions path/to/Portfolio_Positions.csv
```

**`--positions` does three things:**

1. **Verify** — compare replay output vs CSV, report discrepancies:
   - Per-symbol quantity: flag mismatches > 0.001 shares
   - Per-account cash: flag mismatches > $0.01
   - Cost basis: flag mismatches > $1.00
   - Summary: `N/N positions match, M/M cash match, K/K cost basis match`

2. **Calibrate** — overwrite replay values with CSV truth:
   - Positions (qty per symbol per account)
   - Cash balances per account
   - Cost basis per position (from `Cost Basis Total` column — reflects actual lot selection)

3. **Checkpoint** — save calibrated state as new checkpoint. All subsequent incremental builds start from this calibrated point, so cost basis drift doesn't accumulate.

This way:
- **Daily builds** use forward replay + average cost (good enough, no external input needed)
- **Periodic calibration** (whenever user exports positions CSV) snaps everything to Fidelity's ground truth
- Cost basis error resets to zero at each calibration, instead of growing forever

---

## Implementation order

```
P0 (bug fixes):           #1 (5 min), #2 (5 min)
P1 (remove prefix):       ~1-2 hours (pipeline + frontend + worker)
P2 (remove R2):           ~2-3 hours (delete code + migrate /econ)
P3 (automate pipeline):   ~1-2 hours (parameterize + run.sh + launchd)
P4 (checkpoint + verify): ~2-3 hours (checkpoint table + resume logic + verify mode)
```
