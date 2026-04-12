# Ingestion Pipeline Bug Report

**Date**: 2026-04-11
**Scope**: `pipeline/generate_asset_snapshot/` — transaction replay, price fetching, daily allocation
**Triggered by**: Investigating a visible jump in the Safe Net area of the timemachine stacked-area chart around December 2025.

---

## Summary

Five bugs were found in the ingestion pipeline. Three are critical (they silently produce wrong numbers), two are warnings (they hide problems from the operator). All five share a common theme: **the pipeline silently drops data instead of raising errors.**

| # | Severity | One-liner | Impact | Verified |
|---|----------|-----------|--------|----------|
| 1 | Critical | Cost basis wrong for 37/76 positions | $12,410 total drift | Yes — replayed both orderings, compared all 76 positions |
| 2 | Critical | Price download range too narrow — positions vanish | 38 symbols affected; SGOV invisible 50 days (~$24k) | Yes — compared first_held vs actual first txn for all symbols |
| 3 | Critical | Amex HYSA never tracked | ~$15–20k missing from safe_net for 10 months | Yes — replayed Qianji at monthly intervals, cross-checked with DB |
| 4 | Warning  | Positions without prices silently dropped | 3 positions / ~$27.7k dropped on a single sample date | Yes — replayed Nov 10 positions and checked price availability |
| 5 | Warning  | Unmapped Qianji accounts silently dropped | 4 accounts dropped (HYSA, USDC, Venture X) | Yes — scanned Qianji at 4 quarterly dates |

Bugs 1 and 2 share the same root cause: `ORDER BY id` in two functions that assume id order equals chronological order, which is false because CSVs are imported in overlapping batches.

---

## BUG 1 — Cost basis wrong for most positions

### Location

`timemachine.py:148–193`, function `replay_from_db()`

### Root cause

The function fetches all rows with `ORDER BY id`:

```python
# timemachine.py:149–152
rows = conn.execute(
    "SELECT run_date, account_number, action, symbol, lot_type, quantity, amount"
    " FROM fidelity_transactions ORDER BY id"
).fetchall()
```

Then applies cost-basis logic that **depends on processing buys before sells**:

```python
# timemachine.py:174–179
if action_upper.startswith("YOU SOLD") and holdings[key] > 0:
    sold_fraction = min(abs(qty) / holdings[key], 1.0)
    cost_basis[key] -= cost_basis[key] * sold_fraction
elif action_upper.startswith(("YOU BOUGHT", "REINVESTMENT")):
    cost_basis[key] += abs(amt)
holdings[key] += qty
```

When a SELL is processed before its corresponding BUY (because the SELL came from an earlier CSV import and got a lower id), `holdings[key]` is 0 or negative. The guard `holdings[key] > 0` fails, so the cost-basis reduction is **skipped entirely**. The subsequent BUY then adds its full cost basis. Result: cost basis is inflated.

### Why ids are not chronological

Fidelity CSVs are imported in batches. Each batch covers a date range and is assigned auto-increment ids. When two CSVs overlap in date range, the later-imported CSV's transactions for the *same* historical dates get *higher* ids than the earlier-imported CSV's transactions.

Current CSV inventory (16 files):

```
File (3):  2023-03-13 → 2023-03-31   (imported early, low ids)
File (4):  2023-04-03 → 2023-06-30
...
File (1):  2025-09-30 → 2025-12-29   ← overlap with File (13)
File (13): 2025-10-01 → 2025-12-31   ← overlap with File (1)
...
File (16): 2026-04-01 → 2026-04-07   (imported last, high ids)
```

The overlap between File (1) and File (13) means transactions from Oct–Dec 2025 were **inserted twice**, with the second insertion (File 13) deleting and replacing the first. Transactions that appeared in File (13) but originated from October/November end up with higher ids than December transactions from File (1).

Concrete example — SGOV transactions ordered by id:

```
id (low)   12/24/2025  REINVESTMENT   0.775 shares   ← from File (1), low id
id (low)   12/10/2025  SELL          -5.000 shares   ← from File (1), low id
id (low)   12/01/2025  BUY            8.000 shares   ← from File (1), low id
id=11881   11/04/2025  BUY          240.000 shares   ← from File (13), HIGH id
```

### Measured impact

Replaying all transactions `ORDER BY id` vs `ORDER BY date, id` and comparing final cost basis:

```
Total positions:              76
Positions with drift > $0.50: 37
Total absolute drift:         $12,409.95
```

Top offenders:

| Ticker | Cost basis (by id) | Cost basis (by date) | Delta |
|--------|-------------------:|---------------------:|------:|
| FNJHX  | $14,069 | $10,024 | +$4,046 |
| NFLX   | $1,341  | $0      | +$1,341 |
| 254709108 | $994 | $0      | +$994   |
| TCEHY  | $614    | $0      | +$614   |
| PYPL   | $509    | $0      | +$509   |
| PDD    | $502    | $0      | +$502   |
| SGOV   | $25,189 | $24,765 | +$423   |

Many tickers that were fully sold (NFLX, PDD, PYPL, SVIX, AMZN, etc.) show residual cost basis that should be $0 — the sell never reduced it because it was processed first.

### Verified

Replayed all transactions in both orderings and compared cost basis for every position:

```
Positions identical regardless of order: True   (qty is a commutative sum)
Positions with cost basis drift > $0.50: 37 / 76
Total absolute drift: $12,409.95
```

FNJHX walkthrough (largest drift, +$4,046):

```
ORDER BY id processing:
  id=11688  09/30/2025  BUY   +515.907  h=515.9   CB += $6,000
  id=11819  12/03/2025  SELL  -862.185  h=-346.1  CB reduced (h > 0)
  id=11913  10/14/2025  BUY   +342.173  h=0.0     CB += $4,000   ← processed AFTER the sell
  id=12223  03/02/2026  BUY   +840.336  h=842.4   CB += $10,000
  Final CB = $14,069   ← WRONG

ORDER BY date processing:
  09/30/2025  BUY   +515.907  h=515.9   CB += $6,000
  10/14/2025  BUY   +342.173  h=858.1   CB += $4,000
  12/03/2025  SELL  -862.185  h=-4.1    CB reduced by 100%  ← correct: sell > holdings
  03/02/2026  BUY   +840.336  h=836.2   CB += $10,000
  Final CB = $10,024   ← CORRECT
```

### Fix

Change `ORDER BY id` to `ORDER BY substr(run_date,7,4)||substr(run_date,1,2)||substr(run_date,4,2), id` in `replay_from_db()`. The same change is needed in the CSV-based `replay()` function for consistency, though that function reads rows pre-sorted from the CSV file.

---

## BUG 2 — Price download range too narrow, positions vanish for weeks

### Location

`prices.py:62–97`, function `symbol_holding_periods_from_db()`

### Root cause

Same `ORDER BY id` problem. This function determines the date range for which to download prices from yfinance:

```python
# prices.py:67
rows = conn.execute(
    "SELECT run_date, symbol, action, quantity FROM fidelity_transactions ORDER BY id"
).fetchall()
```

It tracks the **first encounter** of each symbol:

```python
# prices.py:83–84
if sym not in first_held:
    first_held[sym] = txn_date
```

Because transactions are processed in id order, the "first encounter" is the transaction with the lowest id — which is **not** the earliest transaction by date.

### Measured impact

```
SGOV: first_held = 2025-12-24 (wrong)    actual first buy = 2025-11-04
VTEB: first_held = 2025-12-22 (wrong)    actual first buy = 2025-12-04
```

The downstream effect:

1. `fetch_and_store_prices()` calls yfinance with `start=2025-12-24` for SGOV
2. `daily_close` table has SGOV prices only from 2025-12-24 onward (73 rows, should be ~110)
3. `allocation.py:166` checks `sym in prices.columns and p_date in prices.index`
4. For any date before 2025-12-24, this check fails → SGOV's 240 shares ($24,096) are **silently dropped** from the portfolio

Timeline of the SGOV gap:

```
2025-11-03  FZFXX = $24,908  (cash from HYSA deposit)
2025-11-04  FZFXX =    $187  (bought 240 SGOV) — but SGOV = $0 in output (no price)
   ...50 days of ~$24k invisible...
2025-12-24  SGOV = $24,529   (prices finally available → position appears)
```

This creates a fake -$24.7k dip on Nov 4 and a fake +$24.5k jump on Dec 24 in the chart.

### Verified

All symbols checked — **38 out of 76** have wrong `first_held` dates:

```
Symbol       first_held (buggy)     actual first txn         Gap (days)
----------------------------------------------------------------------
SCHD         2024-09-30             2024-07-08                       84
GOOGL        2025-09-15             2025-07-09                       68
UNH          2025-06-24             2025-04-17                       68
DLB          2024-03-25             2024-01-17                       68
VXUS         2025-06-24             2025-04-17                       68
QQQM         2024-09-27             2024-07-24                       65
GLDM         2025-12-29             2025-10-29                       61
META         2024-06-26             2024-04-30                       57
QQQ          2024-03-25             2024-01-30                       55
QCOM         2023-12-18             2023-10-26                       53
SGOV         2025-12-24             2025-11-04                       50
TCEHY        2025-02-27             2025-01-08                       50
NFLX         2026-03-03             2026-01-13                       49
PYPL         2024-03-25             2024-02-07                       47
FETH         2025-12-29             2025-11-14                       45
... 23 more symbols ...
```

Replay confirms positions exist before price data starts:

```
replay_from_db(as_of=2025-11-04): ('Z29133576', 'SGOV') = 240.0 shares
  → daily_close has NO SGOV price until 2025-12-24 (50-day gap)

replay_from_db(as_of=2025-12-05): ('Z29133576', 'VTEB') = 219.145 shares
  → daily_close has NO VTEB price until 2025-12-22 (18-day gap)
```

On a single sample date (2025-11-10), **3 positions / ~$27,661 in value** were dropped because of missing prices:

```
SGOV   240.000 shares  NO PRICE → DROPPED  (est. ~$24,120)
GLDM    25.000 shares  NO PRICE → DROPPED  (est. ~$2,355)
GLDM    12.587 shares  NO PRICE → DROPPED  (est. ~$1,186)
```

### Fix

Change `ORDER BY id` to chronological ordering. Alternatively, replace the `first_held` logic with a simple aggregation:

```python
# After the loop:
first_held[sym] = min(first_held.get(sym, txn_date), txn_date)
```

---

## BUG 3 — Amex HYSA never tracked in Safe Net

### Location

`config.json` — missing entries in `qianji_accounts.ticker_map` and `assets`

### Root cause

The Qianji app tracks an "Amex HYSA" (American Express High Yield Savings Account). The allocation code maps Qianji accounts to portfolio tickers via `config.json`:

```python
# allocation.py:191–196
ticker = ticker_map.get(qj_acct)
if ticker and ticker in assets:
    ticker_values[ticker] = ticker_values.get(ticker, 0) + usd_val
elif curr == "CNY":
    ticker_values["CNY Assets"] = ticker_values.get("CNY Assets", 0) + usd_val
# else: SILENTLY DROPPED
```

"Amex HYSA" is not in `ticker_map`, and it's not CNY, so it falls through to the implicit else branch and is **silently dropped**.

### Measured impact

Amex HYSA held $15–20k from January through October 2025:

| Date | HYSA Balance | Reported safe_net | Reported total | Missing % |
|------|------------:|------------------:|---------------:|----------:|
| 2025-01-15 | $15,166 | $12,936 | $183,168 | 7.6% |
| 2025-04-15 | $15,753 | $13,237 | $209,698 | 7.0% |
| 2025-07-15 | $19,644 | $22,415 | $263,039 | 6.9% |
| 2025-10-27 | $20,397 | $38,186 | $313,467 | 6.1% |

On 2025-10-28, the entire $20,397.49 was transferred from Amex HYSA to Chase Debit (Qianji transaction: `transfer $20,397.49 from Amex HYSA to Chase Debit`). After this, the money entered the tracked portion of the portfolio (Chase Debit → "Debit Cash" → Fidelity deposit → SGOV).

The visible chart artifact: a +$20k jump on Oct 28 that looks like money appeared from nowhere. In reality, money moved from an untracked account to a tracked one.

### Verified

Replayed Qianji balances at monthly intervals. Amex HYSA had a real, growing balance throughout 2025:

```
Date           HYSA Balance   safe_net (DB)      total (DB)  HYSA % of corrected total
------------------------------------------------------------------------------------
2025-01-15     $   15,165.77 $   12,935.73 $   183,167.74                       7.6%
2025-04-15     $   15,753.12 $   13,237.38 $   209,698.32                       7.0%
2025-07-15     $   19,643.86 $   22,414.58 $   263,039.27                       6.9%
2025-10-15     $   20,397.49 $   39,421.90 $   305,564.27                       6.3%
2025-10-27     $   20,397.49 $   38,186.45 $   313,466.51                       6.1%
2025-10-28     $        0.00 $   59,492.89 $   334,902.34                       0.0%
```

27 Qianji transactions reference Amex HYSA (deposits from Chase Debit, interest income, credit card repayments). The account accumulated $1,000/month transfers from checking + ~$50/month interest. All were invisible to the dashboard.

The visible chart artifact: a sudden +$20,397 jump in safe_net on Oct 28 when money moved from untracked Amex HYSA to tracked Chase Debit.

### Fix

Add to `config.json`:

```json
// In "qianji_accounts.ticker_map":
"Amex HYSA": "Amex HYSA"

// In "assets":
"Amex HYSA": { "category": "Safe Net" }
```

---

## BUG 4 — Positions without prices silently dropped

### Location

`allocation.py:164–169`

### Mechanism

```python
# allocation.py:164–169
for (_acct, sym), qty in positions.items():
    p_date = mf_price_date if sym in mutual_funds else price_date
    if sym in prices.columns and p_date in prices.index:
        price = prices.loc[p_date, sym]
        if pd.notna(price):
            ticker_values[sym] = ticker_values.get(sym, 0) + qty * float(price)
    # else: NOTHING — no log, no warning, no error
```

When a position exists in the Fidelity replay (e.g., 240 shares of SGOV from Nov 4) but the `daily_close` table has no price for the current date, the position is silently skipped. No entry appears in `computed_daily_tickers`, and the value is excluded from `safe_net` and `total`.

### Why this matters

This bug is what makes Bug #2 invisible to the operator. If the pipeline logged a warning like "SGOV: 240 shares but no price on 2025-11-04", the price-range bug would be immediately obvious. Instead, the pipeline completes successfully and produces plausible-looking (but wrong) numbers.

### Verified

Replayed Fidelity positions as of 2025-11-10. Of 31 positions, 3 had no price data in `daily_close` and were silently excluded:

```
Ticker         Shares Has Price?       Value          Status
SGOV          240.000 ** NO **                DROPPED  (est. ~$24,120)
GLDM           25.000 ** NO **                DROPPED  (est. ~$2,355)
GLDM           12.587 ** NO **                DROPPED  (est. ~$1,186)

With prices:    28 positions
Without prices: 3 positions (SILENTLY DROPPED)
Est. dropped value: ~$27,661
```

No log, no warning, no error was produced anywhere in the pipeline output.

### Fix

Add a warning log when a position with nonzero quantity has no price:

```python
if sym not in prices.columns or p_date not in prices.index:
    log.warning("No price for %s on %s (holding %.3f shares) — excluded from allocation", sym, p_date, qty)
```

---

## BUG 5 — Unmapped Qianji accounts silently dropped

### Location

`allocation.py:191–196`

### Mechanism

```python
# allocation.py:191–196
ticker = ticker_map.get(qj_acct)
if ticker and ticker in assets:
    ticker_values[ticker] = ticker_values.get(ticker, 0) + usd_val
elif curr == "CNY":
    ticker_values["CNY Assets"] = ticker_values.get("CNY Assets", 0) + usd_val
# else: NOTHING — no log, no warning, no error
```

Any USD-denominated Qianji account that is not in `ticker_map` (or whose mapped ticker is not in `assets`) is silently dropped. There is no log, no warning, and no error.

### Current state

As of 2026-04-10, all current Qianji accounts with nonzero USD balances are either mapped or in the skip list. The only historical casualty is "Amex HYSA" which had a balance from 2025-01 through 2025-10 but was never mapped.

However, if a new savings account, brokerage, or other USD account is added to Qianji in the future, it will be silently dropped until someone notices the numbers don't add up and investigates.

### Verified

Scanned all Qianji account balances at 4 quarterly dates in 2025. Found **4 distinct USD accounts** that were silently dropped at some point:

```
Account                   Date           Balance  Status
----------------------------------------------------------------------
Amex HYSA                 2025-01-15   $ 15,165.77  SILENTLY DROPPED
Amex HYSA                 2025-04-15   $ 15,753.12  SILENTLY DROPPED
Amex HYSA                 2025-07-15   $ 19,643.86  SILENTLY DROPPED
Amex HYSA                 2025-10-15   $ 20,397.49  SILENTLY DROPPED
C1 Venture X              2025-01-15   $    762.02  SILENTLY DROPPED
C1 Venture X              2025-04-15   $    745.40  SILENTLY DROPPED
USDC                      2025-10-15   $    500.00  SILENTLY DROPPED
Venture X                 2025-04-15   $    272.30  SILENTLY DROPPED
```

Notes:
- "C1 Venture X" and "Venture X" are credit card accounts that can carry positive balances (rewards, refunds). They are treated as liabilities only when negative, but when positive the balance falls through the else branch and is dropped.
- "USDC" is a stablecoin balance — a real USD-denominated asset not mapped to any category.

### Fix

Add a warning log for unmapped accounts with meaningful balances:

```python
else:
    if usd_val > 10:  # skip dust
        log.warning("Qianji account %r (%.2f USD) has no ticker_map entry — excluded from allocation", qj_acct, usd_val)
```

---

## Root Cause Analysis

### The ORDER BY id assumption

Bugs 1 and 2 share the same root cause. Three functions in the pipeline use `ORDER BY id` to process Fidelity transactions:

| Function | File | Line | Effect of wrong order |
|----------|------|------|-----------------------|
| `replay_from_db()` | `timemachine.py` | 152 | Cost basis drift ($12.4k) |
| `symbol_holding_periods_from_db()` | `prices.py` | 67 | Wrong first-held dates → missing prices |
| `replay()` (CSV variant) | `timemachine.py` | 106 | Not affected (CSV is pre-sorted) |

The assumption that `id` order equals chronological order was likely true when there was a single CSV file. It broke when the pipeline started ingesting multiple overlapping CSVs via `ingest_fidelity_csv()`.

The ingestion function (`db.py:213–294`) handles overlap by **deleting all existing rows in the new CSV's date range**, then inserting the new CSV's rows. This means transactions from the same historical date can get different ids depending on which CSV import they survive through.

Example with SGOV:

```
Import 1: File (1) covers 09/30–12/29/2025
  → SGOV 12/24 reinvestment gets id ~11730
  → SGOV 12/01 buy gets id ~11825
  → SGOV 11/04 buy gets id ~11830

Import 2: File (13) covers 10/01–12/31/2025
  → DELETE all rows from 10/01–12/31
  → Re-insert: SGOV 12/24 reinvestment gets NEW id (still low-ish)
  → Re-insert: SGOV 11/04 buy gets NEW id = 11881 (highest SGOV id)
```

Result: Nov 4 buy has the highest id despite being the earliest transaction.

### The silent-drop pattern

Bugs 3, 4, and 5 share a different root cause: the pipeline uses **conditional inclusion** (only add to the total if X) instead of **unconditional inclusion with validation** (always add, warn if data seems wrong).

This pattern makes the pipeline appear to work correctly even when it's missing significant data. The operator has no signal that anything is wrong until they notice the chart looks off.

---

## Reproduction

### Bug 1 — Cost basis drift

```bash
cd pipeline
.venv/Scripts/python -c "
from generate_asset_snapshot.timemachine import replay_from_db
from pathlib import Path
from datetime import date

result = replay_from_db(Path('data/timemachine.db'), date(2026, 4, 10))
# Check FNJHX cost basis — should be ~$10,024 but shows ~$14,069
for k, v in result['cost_basis'].items():
    if 'FNJHX' in k[1]:
        print(f'{k}: ${v:,.2f}')
"
```

### Bug 2 — Wrong holding periods

```bash
cd pipeline
.venv/Scripts/python -c "
from generate_asset_snapshot.prices import symbol_holding_periods_from_db
from pathlib import Path

periods = symbol_holding_periods_from_db(Path('data/timemachine.db'))
# SGOV first_held should be 2025-11-04, not 2025-12-24
print(f'SGOV: {periods[\"SGOV\"]}')
print(f'VTEB: {periods[\"VTEB\"]}')
"
```

### Bug 3 — Amex HYSA invisible

```bash
cd pipeline
.venv/Scripts/python -c "
from generate_asset_snapshot.timemachine import replay_qianji, DEFAULT_QJ_DB
from datetime import date

qj = replay_qianji(DEFAULT_QJ_DB, date(2025, 10, 1))
print(f'Amex HYSA balance: \${qj.get(\"Amex HYSA\", 0):,.2f}')
# Shows ~$20,338 — this is never included in safe_net
"
```
