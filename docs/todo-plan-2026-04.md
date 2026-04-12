# TODO & Plan — 2026-04

Master tracking for outstanding work. Supersedes ad-hoc notes and `structural-cleanup-plan-2026-04.md` for scope/ordering. The structural plan doc stays as the detailed reference for items 1–11.

Current state (as of 2026-04-12, post-Batch-4):

- Main: `a483b77` (PR #103 merged)
- Merged today: #94–#97 (earlier batches), #98 (PR-X invariant protection), #99 (PR-A quick wins), #100 (PR-C pipeline ingest reorg), #101 (PR-B frontend restructure), #102 (PR-D build script split), #103 (PR-E fidelity ingest natural-key dedup)
- Prod D1: **healthy** — has correct split-reversed historical prices, `is_retirement` flags set, `categories` populated (all done today as quick patches)
- Local `timemachine.db`: **stale** — restored from Apr 9 backup (`timemachine_old.db`) which predates commit `87dea20` (yesterday's split-reversal fix). Contains Adj Close era values. Contains 49 pre-#103 natural-key duplicates that the new `init_db` migration will clean automatically on next rebuild. Sync from this state to prod is **safe for `daily_close`** (INSERT OR IGNORE) but unsafe for other tables — so **don't sync until rebuilt**.

---

## 0. Invariants and guiding principles

These are the contracts we now enforce; any change below must preserve them.

1. **Historical data is immutable.** `daily_close.close` (unadjusted market close) is a physical fact. Rows older than the refresh window (7 days) must not be overwritten by subsequent fetches.
2. **Rebuild from raw inputs + repo seeds must always succeed.** No reliance on local-only state in someone's SQLite file.
3. **Fail loudly at boundaries.** Schema drift, empty Yahoo response, malformed dates — raise with a clear message, do not silently corrupt.
4. **Clean refactors, no backcompat shims.** Delete old code paths when replacing them.
5. **Don't hide errors in UI.** Failed sections render explicit error cards.

---

## 1. Completed

All six short-term PRs from the plan merged today:

| PR | Branch | Items |
|---|---|---|
| #98 | `fix/prices-invariant-protection` | PR-X: daily_close invariant protection (IGNORE historical, REPLACE recent) |
| #99 | `refactor/structural-quick-wins` | PR-A: items 1, 2, 3, 8, 10, 11 |
| #100 | `refactor/pipeline-ingest-reorg` | PR-C: item 5 — `ingest_*` out of `db.py` |
| #101 | `refactor/frontend-restructure` | PR-B: items 4, 6, 7 — schemas/, shared.tsx split, style files |
| #102 | `refactor/build-script-split` | PR-D: `_ingest_and_fetch` → 4 named helpers |
| #103 | `fix/fidelity-ingest-natural-key` | PR-E: INSERT OR IGNORE + natural-key dedup + `init_db` migration |

Test counts on main: pytest **466**, vitest **115**. All typecheck + lint green.

---

## 1b. Historical reference: PR-X — daily_close invariant protection

**Branch**: `fix/prices-invariant-protection` (local only)

**What**: Split `INSERT OR REPLACE` into `OR IGNORE` for historical dates and `OR REPLACE` for recent-window dates in both `fetch_and_store_cny_rates` and `fetch_and_store_prices`.

**Why**:
- Yahoo occasionally returns partial/wrong data (confirmed today — 719 instead of 801 rows for CNY=X).
- Current code overwrites good stored data with bad fetch.
- Prod already has correct historical prices; this PR makes sure no future fetch can corrupt them.

**Status**:
- 4 tests written (`TestHistoricalImmutabilityCnyRates` + `TestHistoricalImmutabilityPrices`), 2 failing against current code (confirms they actually test the invariant).
- Implementation not yet written.

**Design**:
- Module-level `REFRESH_WINDOW_DAYS = 7`.
- Helper `_persist_close(conn, symbol, date_iso, close, refresh_cutoff_iso)` — dispatch to OR IGNORE vs OR REPLACE based on date.
- `fetch_and_store_cny_rates`: remove cache-range skip (always fetch; the new write semantics make refetch idempotent), route every row through the helper.
- `fetch_and_store_prices`: keep the batch cache-range check (optimization: skips 83 symbols' fetch if fully cached), but route writes through the helper.
- Leave `_reverse_split_factor` alone — it's correct.

**Does NOT do**:
- Does not fix stale data already in local DB (that's a rebuild problem).
- Does not add retry logic (separate concern — see §4).
- Does not touch `ingest_fidelity_csv` (that has its own range-replace issue — see §2 PR-E).

---

## 2. Short-term PR queue (after PR-X)

Ordered by dependency and risk. Each PR standalone; sub-agent-friendly or hand-written.

### PR-A — Structural quick wins
From `structural-cleanup-plan-2026-04.md` items 1, 2, 3, 8, 10, 11 (header tweak).
- Items 1 (test location), 2 (empty test dirs), 3 (screenshot scripts), 8 (flatten `core/reconcile.py`), 10 (config co-location), 11 (schema.sql header comment).
- All XS effort, zero risk. Single PR with one commit per item.

### PR-B — Frontend restructure
Items 4, 6, 7 — all frontend file moves/renames.
- 4: `schema.ts` + `econ-schema.ts` → `src/lib/schemas/` directory.
- 6: Split `shared.tsx` into `section.tsx` + `ticker-table.tsx`.
- 7: Rename/merge `style-helpers.ts` + `chart-styles.ts` (thresholds.ts + theme.ts, or merge into hooks.ts).

### PR-C — Pipeline ingest reorganization
Item 5 — move `ingest_*` functions from `db.py` to their respective `ingest/*.py` modules.
- Non-trivial refactor; test fallout expected.
- `db.py` retains only DDL + connection helpers.

### PR-D — Split `_ingest_and_fetch` in build script
`pipeline/scripts/build_timemachine_db.py:226-283` — the 58-line orchestrator doing 6 things. Break into:
- `_init_and_ingest_fidelity`
- `_ingest_empower_401k`
- `_fetch_all_prices` (computes holding periods, merges proxy + robinhood symbols)
- `_compute_401k_daily`

### PR-E — Fidelity ingest hardening
The current `ingest_fidelity_csv` uses `DELETE WHERE run_date BETWEEN ? AND ? ; INSERT`. If a new CSV happens to be a subset of what we have (Fidelity revises/removes a row), the DELETE wipes the old row and INSERT doesn't restore it → silent data loss.

Fix:
- Natural-key dedup: `(run_date, action, symbol, amount, quantity, price)` as idempotency key.
- `INSERT OR IGNORE` by composite key.
- Higher risk than other PRs — this is where the pre-PR-#94 duplicate bug lived; design carefully.

### PR-F — CLAUDE.md + structural-cleanup doc updates
After all above land:
- Update `CLAUDE.md` with the new structure.
- Mark completed items in `structural-cleanup-plan-2026-04.md`.
- Strike item 12 (CNY seed file) — obsoleted by PR-X invariant protection. Write a short note why.

---

## 3. Medium-term (after PR queue above)

### Local DB rebuild (one-shot operation)
Not a PR — a manual step the user does after PRs A–E are merged.

1. Back up: `cp pipeline/data/timemachine.db pipeline/data/timemachine.db.bak`
2. `rm pipeline/data/timemachine.db`
3. `cd pipeline && python3 scripts/build_timemachine_db.py full`
4. **Parity check — MANDATORY before sync.** Compare local DB against prod D1 on the tables that would be written by sync. Prod is currently the source of truth for historical correctness; if local differs from prod on overlapping rows, **investigate before syncing**.
   - Spot-check: SCHD 2024-10-01 should be ~$84.48 in both (not $26.62)
   - Full parity check: a dedicated script `pipeline/scripts/verify_vs_prod.py` (to be written) that:
     - Samples random rows from `daily_close`, `computed_daily`, `fidelity_transactions`, `qianji_transactions`
     - Queries prod D1 via `wrangler d1 execute --remote --json`
     - Reports any mismatch beyond a small float tolerance
     - Exits non-zero if any mismatch found
   - Must-be-green categories:
     - Historical `daily_close` rows (dates < today − 7) must match to 4 decimal places
     - `computed_daily.total` must match to the cent (or within $1 for float rounding)
     - Fidelity transaction count in overlapping range must match
5. If parity passes: `python3 scripts/sync_to_d1.py --remote`. Even with parity OK, trust the invariant protection: `daily_close` / `computed_daily` / `computed_daily_tickers` use INSERT OR IGNORE — prod's correct rows stay intact either way. Range-replace tables (fidelity/qianji) will be replaced within local's date range, which is fine because parity already confirmed they match.
6. If parity fails: **stop**. Don't sync. Investigate the diff. The stale local (Apr 9 backup) is going away when rebuilt; any remaining mismatch after rebuild means either prod is wrong or rebuild produced wrong values — both need diagnosis, not "fix with sync".

### Big refactor candidates (defer, need discussion before committing)

These are real improvements but have scope concerns; don't start without a design conversation.

**R1. Two-column `daily_close` (close + adj_close)**
- Store both Yahoo's raw Close and Adj Close.
- Delete `_reverse_split_factor` — Yahoo becomes the source of both.
- Frontend/allocation picks based on need (net worth → close; rate-of-return → adj_close).
- Requires: schema migration, D1 migration, pipeline change, worker schema.ts update.
- Value: simpler, no local math, truly "Yahoo-derived" data.
- Cost: non-trivial migration across both environments.

**R2. Retry + validation layer for Yahoo fetches**
- After `yf.download`, assert that returned dates cover the requested range (at least the weekdays).
- Retry 2–3 times on missing coverage.
- After final attempt, raise a descriptive error instead of silently proceeding to allocation compute.
- Complements PR-X (invariant protection) — PR-X saves existing data; this prevents stale DB from being accepted as "good".

**R3. Profile-based: pipeline performance improvements**
Items C + D originally deferred from Batch 4 pending profiling:
- C: push per-date category aggregation from Python into SQL `SUM(CASE WHEN category=...)`.
- D: use SQL window function for 52w high/low instead of Python loop.
- Profile first. If `compute_daily_allocation` runtime is >10s, worth doing; if <5s, skip.

**R4. Rename `generate_asset_snapshot/` package** (structural item 9)
- Big blast radius (every import). Defer until other changes stable.

---

## 4. Not doing (explicit)

- **CNY manual_rates.csv seed file** (structural plan item 12): approach abandoned. Replaced by PR-X invariant protection + R2 retry/validation. Rationale: Yahoo actually has full history; today's "missing data" was transient flakiness. Seed file was treating a symptom, not the cause.
- **Force-resync old Adj Close era data from prod to local**: prod is correct; local is stale. The fix is local rebuild, not reverse-sync.
- **Migrate existing DB entries to Two-column `daily_close`**: if we ever do R1, that's a separate migration PR.

---

## 5. Dependency graph

```
PR-X (invariant protection)  ← prerequisite for safe resync
    │
    ├── PR-A (quick wins)           ← independent
    ├── PR-B (frontend)             ← independent
    ├── PR-C (pipeline ingest)      ← independent
    ├── PR-D (build script split)   ← independent
    └── PR-E (fidelity ingest)      ← independent but higher risk

After all above merged:
    Local rebuild (manual)
    └── PR-F (doc updates)

Deferred, need design convo:
    R1, R2, R3, R4
```

All of A/B/C/D/E can run in parallel via separate worktrees — no file overlap between frontend (B) and pipeline (C/D/E).

---

## 6. Open questions for user

1. **R1 (two-column `daily_close`)**: worth the migration? Or is "invariant protection + retry" enough for correctness, leaving the current single-column design in place?
2. **R4 (package rename)**: at what point is the blast radius worth paying? Before or after more feature work?
3. **Local DB rebuild timing**: do it now (after PR-X), or batch with PR-F cleanup?
4. **R2 (retry/validation)**: bundle with PR-X, or separate follow-up PR? Bundled = one change to `prices.py`; separate = easier to revert retry logic if it turns out to be flaky in CI.
