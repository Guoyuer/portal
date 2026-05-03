# Historical Rebaseline Report - 2026-05-03

## Summary

Do not publish a full historical rebaseline as part of the correctness fix PR.
The low-risk fixes improve current correctness with tiny current-value impact,
while a full historical recompute moves many old days for mixed reasons.

The safe sequence is:

1. Fix Fidelity canonical ingest, amountized positions verification, and Qianji
   local-day replay correctness.
2. Keep production on the refresh-window path.
3. Treat full historical backfill as a separate data migration that requires an
   explicit drift report and acceptance.

## Current Correctness Fix Impact

Fidelity canonical ingest on the current Downloads set:

- Raw parsed Fidelity CSV rows: 2,404.
- Canonical retained rows: 1,804.
- Current range-replace DB rows: 1,794.
- Net added rows: 10.
- Latest Fidelity snapshot (`Portfolio_Positions_May-03-2026.csv`) passes after
  canonical ingest under `$1 / 0.001 share` verification.
- Before the fix, the current DB missed `Z29133576 / MAR` by `0.015` shares,
  worth about `$5.32` at the May 3 snapshot price.

Current computed window value impact from that MAR row is approximately:

| Date | Value Delta |
|---|---:|
| 2026-04-27 | +$5.41 |
| 2026-04-28 | +$5.37 |
| 2026-04-29 | +$5.31 |
| 2026-04-30 | +$5.43 |
| 2026-05-01 | +$5.32 |

Qianji no-cache replay impact against the existing historical range:

- Changed trading days: 22.
- Max absolute daily total delta: `$1,500.00`.
- Sum of absolute daily total deltas across changed days: `$6,067.19`.
- Average absolute changed-day delta: `$275.78`.

Largest Qianji cache-induced daily deltas:

| Date | Direct Replay Minus Cached |
|---|---:|
| 2024-11-07 | -$1,500.00 |
| 2024-11-19 | -$943.38 |
| 2025-03-05 | +$853.00 |
| 2024-07-12 | -$557.00 |
| 2025-02-13 | -$549.39 |
| 2026-01-21 | -$547.19 |
| 2024-05-14 | +$285.50 |
| 2025-05-20 | -$153.36 |
| 2025-05-08 | -$129.81 |
| 2025-10-07 | -$82.95 |

These Qianji differences are real correctness fixes for the local-day replay
path, but they are not evidence that every full historical recompute output is
the better production truth.

## Full Historical Recompute Drift

Artifact comparison used fixed `version=compare` and
`generated_at=2026-05-03T00:00:00Z` to avoid timestamp noise.

Top-level artifact sections unchanged:

- `categories`
- `empowerContributions`
- `fidelityTxns`
- `holdingsDetail`
- `market`
- `qianjiTxns`
- `robinhoodTxns`
- `syncMeta`

Changed sections:

- `daily`
- `dailyTickers`
- `manifest.json`
- `reports/export-summary.json`

Daily drift:

- Common daily rows: 820.
- Changed dates: 729.
- Max absolute daily `total` delta: `$5,381.80`.
- Median absolute changed-day `total` delta: about `$3,200.00`.
- Sum of absolute daily `total` deltas: about `$1,539,367.34`.

Ticker drift:

- Old `dailyTickers` rows: 32,583.
- New `dailyTickers` rows: 32,920.
- Net added rows: 337.
- Added ticker-date rows: 366.
- Removed ticker-date rows: 29.
- Value-changed ticker-date rows: 1,800.

Largest net ticker deltas in the full recompute experiment:

| Ticker | Net Delta |
|---|---:|
| Debit | -$1,367,819.42 |
| Debit Cash | +$1,327,905.03 |
| F | +$24,054.81 |
| NVDA | +$23,851.08 |
| BABA | +$22,140.46 |
| ADBE | +$14,841.31 |
| U | +$10,400.10 |
| AMZN | +$6,648.22 |
| AI | +$6,224.40 |
| DUOL | +$3,981.44 |

## Attribution

Known causes:

- Fidelity: current DB missed a valid 2025-09-30 MAR dividend reinvestment
  because a partial overlapping CSV range-deleted it.
- Qianji: allocation cache used UTC transaction dates while
  `qianji_balances_at()` uses the user's local-day cutoff.
- Robinhood: historical transactions exist in `robinhood_transactions`, but
  old `computed_daily_tickers` rows were never backfilled for early holdings
  such as F, SONY, and early QQQM.
- Qianji reverse replay: full recompute starts from live `user_asset` balances
  and reverses bills. This is useful for point-in-time replay, but it is not a
  stable byte-for-byte historical oracle if live balances, account metadata, or
  historical bills are corrected later.

No direct evidence was found that active Qianji bills were edited after
creation: `user_bill.updatetime` is empty/zero for active rows in the inspected
database. Some rows have `createtime` later than bill `time`, which can mean
late entry or sync semantics, but it does not prove an edit to an old row.

## Recommended Acceptance Gate

Before accepting any historical rebaseline:

1. Run the build on a copied DB only.
2. Export artifacts with fixed version and generated timestamp.
3. Produce a daily and ticker drift report.
4. Verify latest Fidelity positions pass under `$1 / 0.001 share`.
5. Verify the last refresh window is unchanged except for explained Fidelity or
   Qianji correctness fixes.
6. Explicitly approve the historical drift before publishing artifacts.

## Decision

Accept the current correctness fixes first. Defer full historical backfill until
the drift report is reviewed as a data migration.
