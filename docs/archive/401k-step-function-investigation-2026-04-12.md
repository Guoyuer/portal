# 401k Step-Function Investigation — 2026-04-12

> **Status: Option A implemented 2026-04-13** (`cleanup/audit-completion` PR).
> `_check_day_over_day` is now anchored to the latest `computed_daily` date
> and suppresses anomalies older than 7 days. Eliminates 100% of the noise
> from the 3 historical 401k-snapshot step-functions in sync emails. Option B
> (smoothing contributions between QFX snapshots) remains deferred.

**Question**: every sync email contains 3 persistent day-over-day warnings (2023-07-05, 2024-02-29, 2025-02-28 all >15% / >$5K). Is this a data bug?

**Short answer**: **not a correctness bug** — net worth totals are right at every date. But the *shape* of the curve has 3 one-day stair-steps where 401k contributions lumpily materialize. Validation correctly flags them, but they're not actionable and shouldn't appear in every email.

---

## Observations

Per `pipeline/data/timemachine.db` (as of 2026-04-12):

| Jump date | Total before → after | Δ | Dominant ticker(s) |
|---|---|---|---|
| 2023-07-04 → 2023-07-05 | $51,832 → $59,988 | +$8,157 (+15.7%) | `401k sp500` 0 → $4,401; `401k ex-us` 0 → $2,239; `401k tech` 0 → $1,618 |
| 2024-02-28 → 2024-02-29 | $105,269 → $122,434 | +$17,165 (+16.3%) | `401k sp500` $25,688 → $38,672 (+$12,984); `401k tech` $5,442 → $6,920 |
| 2025-02-27 → 2025-02-28 | $206,318 → $237,332 | +$31,015 (+15.0%) | `401k sp500` $63,834 → $78,286; `Debit Cash` +$5,210; `FZFXX` +$4,083; VOO/QQQM also step up |

Subtotal of 401k-labeled tickers alone accounts for >90% of each jump.

## Root cause

The 401k value uses **quarterly QFX snapshots** from Empower + **proxy interpolation** between them:
- Between snapshots: day `d`'s 401k value = `last_snapshot_value * (proxy_index[d] / proxy_index[last_snapshot_date])`
- At snapshot day: the true QFX-reported value snaps in, often larger than the interpolated path because **contributions that accumulated in the gap are realized all at once**

Specifically for 2023-07-05: it's the **very first date we have 401k data**. Before that date, the 401k value is legitimately zero in our local model. On 2023-07-05 the first Empower QFX snapshot materializes ~$8K of already-accumulated positions.

For 2024-02-29 and 2025-02-28: likely Q4→Q1 snapshot boundaries where quarterly contributions are integer-added to positions, while proxy-interpolation only smoothed the *market movement* component between the prior snapshot and this one. Contributions aren't smoothed into the intervening days.

The `pipeline/etl/timemachine.py` or 401k-related logic has the QFX snapshot dates; a single SQL query on `empower_snapshots` would confirm exact QFX dates match these boundaries.

## Option A (low cost, partial relief) — already queued for S8

Tell `validate_build` / `extract_validation_warnings` to only surface day-over-day warnings for dates within **last 7 days** (same window as the data-immutability invariant from PR #98). Reasoning:
- Historical day-over-day anomalies are not actionable (data is already immutable)
- Only recent warnings let a user actually investigate / fix incoming pipeline data
- Eliminates 100% of this email noise

**Status**: being added to PR S8 scope (email polish). ~10 LOC.

## Option B (real fix) — deferred, evaluate tomorrow

Redistribute contributions smoothly between snapshots instead of letting them land on snapshot day.

### Design sketch

```
Current:
  401k value on day d =
    last_snapshot_value * (proxy_index[d] / proxy_index[last_snapshot_date])

Proposed:
  Let C_total = contributions_between(last_snapshot, next_snapshot)
      N = days in that interval
      contributions_per_day = C_total / N          (linear) or pro-rata by pay period

  401k value on day d within interval =
    last_snapshot_value * (proxy_index[d] / proxy_index[last_snapshot_date])
    + sum(contributions_per_day for days in [last_snapshot_date+1 .. d])
      * (proxy_index[d] / proxy_index[midpoint])      (simpler: just linear)
```

### Inputs we have

- `empower_contributions`: per-date BUYMF transactions (from QFX) with date + amount + ticker
- `empower_snapshots`: quarter-end snapshot values per fund
- Qianji `401k` account entries as backup contribution data when QFX is missing

### Complications

1. **First-snapshot boundary (2023-07-05)**: we have no 401k data before this. Genuine gap, can't smooth. Option: just exclude pre-first-snapshot dates from the validation window. Fine.

2. **Contribution timing**: user's real contributions hit the 401k every paycheck (bi-weekly). QFX records them on actual transaction dates. Between snapshots, we have transaction-level data. So contributions can be added to proxy-interpolated values on their real date rather than pooled at snapshot day.

3. **Price at contribution date**: each contribution converts USD → fund shares at that day's NAV. We'd need fund share price history. Approximation: use the proxy index to convert contribution $ to "shares at that day's proxy price", then carry forward.

4. **Validation drift**: if we smooth differently than QFX reports, the snapshot-day total will differ from QFX's reported value. Would need to reconcile by letting snapshot day override our smoothed estimate (snap to QFX ground truth).

### Work estimate

- Refactor `daily_401k_values()` in `pipeline/etl/empower_401k.py` to fold contributions by day instead of lumping at snapshot dates
- Add tests using synthetic contribution/snapshot fixtures
- Re-backfill `computed_daily` / `computed_daily_tickers` for all dates (full rebuild)
- Re-sync prod (via existing `sync_to_d1.py`)
- ~300 LOC across module + tests

### Value

- Net worth curve becomes truly smooth (no 3 yearly step-functions)
- Dashboard charts more faithful to economic reality
- Eliminates the false-warning class entirely (Option A addresses the *symptom*; B addresses the *cause*)

### Counter-argument (do NOT do)

- Total net worth is correct today; only the *path* is stair-stepped
- Quarterly stair-step is reality if someone only cares about point-in-time values
- 3 events per 2+ years isn't visually catastrophic
- QFX snapshot is authoritative; any smoothing we do is inherently an estimation
- If user likes to visually see "ah, end of Feb my 401k showed up", that's a feature

### Decision criteria for tomorrow

Look at the current chart on https://portal.guoyuer.com — are the 3 steps visually distracting on the net-worth timeline? If yes, do B. If they blend in or add useful "income-event" markers, skip B and rely solely on A.

Also: when next QFX lands (~end of Q2 2026), will the step recur? Yes — every 3 months, forever. So A is a band-aid; B is the only architectural fix.

---

## My lean

**If you care about visualization quality: do B next month, not tomorrow.** Tomorrow you already have Option A in S8. That suppresses the noise. Then B becomes a standalone architectural improvement you can schedule when you have a half-day slot.

**If you don't care about chart smoothness**: A alone is enough. Close this doc.
