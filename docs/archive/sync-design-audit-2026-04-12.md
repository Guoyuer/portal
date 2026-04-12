# Sync Design Audit — 2026-04-12

> **ARCHIVED 2026-04-12**: findings executed via PRs #109–#114. See docs/plan-automation-readiness-2026-04-12.md for the execution record.

**Scope**: verify the 7 critiques I raised about the current DB + sync design, audit ingestion pipeline for complexity / error-proneness, and assess readiness for Windows Task Scheduler automation.

**Primary use case we're optimizing for**: user drops a new Fidelity CSV into `~/Downloads/`, a scheduled task picks it up, builds, and syncs to prod D1 — **without human intervention and without data loss**.

---

## TL;DR

Of the 7 original critiques:

| # | Claim | Verdict |
|---|---|---|
| 1 | Sync not atomic across wrangler's internal chunks | **CORRECT** (I also incorrectly said "no transaction boundary" — corrected mid-convo) |
| 2 | Range-replace assumes local ⊇ prod, can silently wipe prod | **CORRECT** (worst case in automation flow) |
| 3 | `--diff` flag does 3 different things in one invocation | **CORRECT** |
| 4 | No built-in parity check before sync | **CORRECT** (`verify_vs_prod.py` still TODO) |
| 5 | View migration path doesn't exist | **CORRECT** (`CREATE VIEW IF NOT EXISTS` silently no-ops if view exists) |
| 6 | `sync_meta` string-concat injection risk | **CODE SMELL** (not real injection, inconsistent style) |
| 7 | `econ_series` full-replace | **PARTIALLY WRONG** (FRED DOES revise — full-replace is actually right for this data; the real bug is that `run.sh` skips FRED refetch in incremental mode) |

**Additional critical bugs discovered during audit** (not in original 7):

- **🔥 B1 — `run.sh` never syncs new Fidelity/Qianji transactions.** It computes `LAST_DATE = MAX(date)` from `computed_daily` **after** the build, which equals today. Then passes `--since $LAST_DATE` → range-replace condition `WHERE run_date > today` matches 0 rows. New trades get into local DB but never leave it.
- **🔥 B2 — Default `sync_to_d1.py` (no flags) full-replaces `fidelity_transactions` / `qianji_transactions`.** For the incremental-CSV workflow where local ingest may not cover prod's full range, this **wipes prod's historical txns** with local's subset. This is a loaded foot-gun hiding behind the default invocation.
- **B3 — `run.sh` change-detection ignores Empower QFX files.** Drop a new QFX → `run.sh` does nothing → 401k data goes stale until a force-run.

**Overall recommendation**: the current sync layer is too loaded with sharp edges to be driven by cron. Fix B1/B2 + rewrite `run.sh` to Windows-native before automating.

---

## Verified Claims

### Claim 1 — Sync atomicity under wrangler

**What I said**: "wrangler chunks SQL; each chunk is atomic but cross-chunk is not."

**Verdict**: **CORRECT**.

**Evidence**: Cloudflare's D1 import path (`wrangler d1 execute --file`) uploads to R2 and invokes the server-side importer, which splits the file into per-chunk transactions. Per the [D1 import/export docs](https://developers.cloudflare.com/d1/best-practices/import-export-data/), chunks commit independently; a mid-file failure leaves partial state. D1 explicitly forbids `BEGIN TRANSACTION` / `COMMIT` in import files ("cannot start a transaction within a transaction").

For a single `POST /query` (Workers binding `batch()`), SQL is atomic within that HTTP request. But `d1 execute --file` is not a single request.

**Limits also confirmed** (docs, 2026): max 100KB per SQL statement, 30s per statement, max 5GiB per imported file (gated by R2), no "rows affected" limit but practical advice to chunk at ~1k rows.

**Impact on primary use case**: low in practice — our sync SQL is ~5MB, chunks succeed individually, and retrying the whole file is idempotent for `INSERT OR IGNORE` tables. But for `_RANGE_TABLES`, a mid-chunk failure between DELETE and INSERT would leave prod missing the just-deleted range until next full re-run.

---

### Claim 2 — Range-replace subset risk

**What I said**: "if local is incomplete in the `--since` range, the sync silently wipes prod rows."

**Verdict**: **CORRECT**.

**Evidence**: `pipeline/scripts/sync_to_d1.py:114-130` — `_dump_table_range` emits `DELETE FROM {table} WHERE {date_expr} > '{since}';` followed by an `INSERT` for local rows where `date_expr > since`. The DELETE fires unconditionally; no check that local's coverage equals or exceeds what it's about to delete in prod.

**Concrete failure scenario**:
- Prod `fidelity_transactions`: rows from 2024-01-01 through 2026-03-31
- User's `~/Downloads/` has only `Accounts_History_2026-Q1.csv` (2026-01-01 — 2026-03-31)
- Local DB after incremental build: fidelity rows for 2026-01-01..2026-03-31 only (range-replace on local wiped older rows? No — local ingest does `DELETE BETWEEN min_date AND max_date` from CSV, so older CSVs that weren't re-ingested stay intact — BUT if local was freshly rebuilt from only this one CSV, pre-2026 rows are missing)
- `sync_to_d1.py --diff --since 2024-01-01` → `DELETE FROM fidelity_transactions WHERE run_date > '2024-01-01'` → wipes 2024 through today → `INSERT` only 2026-Q1 rows → **prod loses 2024 and 2025 entirely**.

This is the exact bug PR #103 tried to solve via natural-key dedup on the ingest side; the revert (PR #105) kept range-replace + relied on `verify_positions.py` as safety net. But `verify_positions.py` runs after build, not after sync — it's the wrong insurance policy for this risk.

---

### Claim 3 — `--diff` flag does 3 things

**What I said**: "one flag, three different semantics depending on table category."

**Verdict**: **CORRECT**.

**Evidence**: `sync_to_d1.py:165-177` main loop:
- `_DIFF_TABLES` (`computed_daily`, `computed_daily_tickers`, `daily_close`): `_dump_table_diff` → `INSERT OR IGNORE`, no DELETE. `--since` ignored.
- `_RANGE_TABLES` (`fidelity_transactions`, `qianji_transactions`): `_dump_table_range` → `DELETE WHERE col > since; INSERT`. Requires `--since`.
- Everything else (categories, market_*, holdings, econ_series): `_dump_table` → full replace.

**Also**: `--diff` without `--since` errors loudly (exits before wrangler executes), but only once the loop reaches a range-replace table. No early validation. Minor but fixable.

---

### Claim 4 — No parity check

**What I said**: "no `verify_vs_prod.py`, all local↔prod discipline is manual."

**Verdict**: **CORRECT**.

**Evidence**: Grep for `verify_vs_prod` finds only `docs/todo-plan-2026-04.md:127` (described as "to be written") and `docs/ARCHITECTURE.md`. No such script exists in `pipeline/scripts/`. `verify_positions.py` only validates local DB against Fidelity Portfolio_Positions CSV — it doesn't touch prod D1.

**Impact on automation**: significant. A scheduled task can't "know" if local is divergent from prod without a pre-sync check. Today this was enforced by hand (spot-checking SCHD price, comparing row counts). A cron can't do that.

---

### Claim 5 — View migration path

**What I said**: "`CREATE VIEW IF NOT EXISTS` in `worker/schema.sql` doesn't update existing views."

**Verdict**: **CORRECT**.

**Evidence**:
- `worker/schema.sql:111-174` — all views use `CREATE VIEW IF NOT EXISTS`
- Zero `DROP VIEW` anywhere in repo (grep confirms)
- `sync_to_d1.py` syncs only the 10 data tables (lines 32-43); `schema.sql` is never executed during sync
- `ci.yml:88-96` — Worker deploy runs `wrangler deploy` only; doesn't apply schema
- Propagation requires manual `wrangler d1 execute portal-db --remote --file=worker/schema.sql` AND manual `DROP VIEW v_foo` first if the view already exists

**Impact on primary use case**: low — we don't change views often. But the drift risk is silent (old view definition stays in prod, new code in local). Worth documenting at minimum.

---

### Claim 6 — `sync_meta` injection risk

**What I said**: "string-concat SQL, potential injection."

**Verdict**: **CODE SMELL, not real injection.**

**Evidence**: `sync_to_d1.py:181-189` — `now` is an ISO timestamp (safe format), `last_date` is a TEXT date from `computed_daily.date` (written via parameterized INSERT in `build_timemachine_db.py:386-391`). Raw source values cannot contain a `'`.

But: `_escape` exists (lines 66-73) and is used for all other table rows via `_dump_table`. Using it here too is one line of code and removes the foot-gun a copy-paste would otherwise create. Keep the finding but downgrade severity to "consistency fix."

---

### Claim 7 — `econ_series` full-replace

**What I said**: "append-only monthly data, should be OR IGNORE."

**Verdict**: **PARTIALLY WRONG.**

**Why**: FRED actually does revise historical values (CPI revisions, unemployment revisions, Fed rate post-meeting corrections). `INSERT OR IGNORE` would keep stale numbers in prod indefinitely. Full-replace is semantically the right call.

**The real bug**: `precompute_market` (which writes `econ_series` at `precompute.py:198-207`) runs in BOTH `_full_build` and `_incremental_build` (`build_timemachine_db.py:414, 455`), so this path is actually exercised on every `run.sh`. So `econ_series` DOES get fresh FRED data each run and this claim was a false alarm.

**Net**: my claim was wrong, but the agent flagged a related but different concern (FRED revision awareness) which is fine as-is given current flow.

---

## Bonus Bugs (not in original 7)

### 🔥 B1 — `run.sh` never syncs new Fidelity/Qianji transactions

**File**: `pipeline/scripts/run.sh:97-103, 117`.

```bash
# Runs AFTER incremental build
LAST_DATE=$(SELECT MAX(date) FROM computed_daily)  # = today after build
"$PYTHON" sync_to_d1.py --diff --since "$LAST_DATE"
```

`sync_to_d1.py --diff --since <today>`:
- Range-replace for `fidelity_transactions` → `DELETE WHERE run_date > '<today>'; INSERT WHERE run_date > '<today>'` → **zero rows both sides**.
- Same for `qianji_transactions`.

**Consequence**: the frontend's Activity section (built from `v_fidelity_txns`) never sees newly ingested trades until someone manually runs a full sync. The net-worth totals ARE correct because `computed_daily_tickers` uses `INSERT OR IGNORE` diff sync. So the bug is silent — a user would see allocation update but the trades list go stale.

**Fix**: `LAST_DATE` should be captured BEFORE the build (= `get_last_computed_date()` prior to `append_daily`), or `run.sh` should use a different cutoff — e.g., `min(get_last_computed_date_BEFORE_build, max(run_date in new CSVs) - 7)`. Simplest correct choice: use `LAST_DATE - N days` for some small N that guarantees coverage (7 days matches the `REFRESH_WINDOW_DAYS` invariant).

---

### 🔥 B2 — Default `sync_to_d1.py` (no flags) is destructive

**File**: `pipeline/scripts/sync_to_d1.py:175-177`.

Without `--diff`, every table goes through `_dump_table()` = `DELETE FROM t; INSERT ...`. For `fidelity_transactions` / `qianji_transactions`, this replaces prod's entire history with local's current content.

**Foot-gun for automation**: any scheduled task that accidentally invokes `sync_to_d1.py` without flags (e.g., cron-line forgets `--diff`, or a human-run recovery skips the flag) wipes prod's superset.

**Fix options**:
- Make `--diff` the default; require explicit `--full` for destructive mode.
- Or: refuse to run without any flag; require explicit `--full` OR `--diff`.

Today's state is "no-flag does the most dangerous thing" — textbook CLI design anti-pattern.

---

### B3 — Change detection skips Empower QFX

**File**: `pipeline/scripts/run.sh:46-72`.

`changes_detected()` checks Qianji DB mtime + `Accounts_History*.csv` newness. It does NOT check `Bloomberg.Download*.qfx` files. User drops a new quarterly 401k QFX → `run.sh` says "no changes" → 401k data stays stale until `--force`.

---

## Ingestion Pipeline Complexity — Targets for Simplification

From the agent audit (ranked by ROI to primary use case):

1. **Delete dead code**: `--positions` flag is parsed but never used (`build_timemachine_db.py:92`). `verify` mode has no automation path and no "ship results" — dead in practice (`build_timemachine_db.py:474-489, 517-518`). ~50 lines, trivial cost, zero behavior change.

2. **Fix `run.sh` LAST_DATE bug** (B1 above). Or replace `run.sh` entirely with a Windows-native wrapper that does it right (see §Automation).

3. **Collapse 3 build modes into 2**: `full | incremental | verify` → `full | incremental`. Verify mode isn't used. Three paths (`_full_build`, `_incremental_build`, `_verify_build`) that all call `compute_daily_allocation()` and differ only in persist strategy. Could be one function with a mode parameter.

4. **Add Empower QFX to change detection** (fix B3). One line in `run.sh`.

5. **Silent fallback to full build on empty DB**: `_incremental_build` → `_full_build` fallback is silent. Add a `print("  First run — falling back to full build")` — 1 line.

6. **Redundant date derivation**: `_derive_start_date` queries `MIN(run_date)` AFTER `_ingest_fidelity_csvs` already sorted CSVs by start date. Could pass through instead of requery.

---

## Automation Readiness — What's Blocking Windows Task Scheduler

**Non-negotiables for unattended operation**:

| Requirement | Status |
|---|---|
| Default command is idempotent & non-destructive | ❌ B2: no-flag destroys prod |
| Default command actually syncs new data | ❌ B1: new fidelity txns never reach prod |
| Detects all change sources (Fidelity, Qianji, Empower) | ❌ B3: QFX skipped |
| Logs to a file for postmortem | ❌ `run.sh` writes to stdout only |
| Exit codes distinguish "no change" / "success" / "failure" | ⚠️ `run.sh` uses `set -e` but exit code 0 on "no changes" is indistinguishable from success |
| Schema drift detection | ❌ No view migration path; no pre-sync parity check |
| Parity check before destructive sync | ❌ `verify_vs_prod.py` not written |
| Bash available on Windows | ⚠️ MSYS/Git-Bash works but is not native; not how people write Windows scheduled tasks |

**Conclusion**: `run.sh` is not cron-ready. It was probably fine as a manual convenience wrapper but should be replaced, not patched.

---

## Recommended Action Plan (ordered)

### Phase 1 — De-fang the CLI (before any automation)

**P1.1** Fix B2: make `--diff` the default in `sync_to_d1.py`. Rename current default to `--full` and require it explicitly. Update tests.

**P1.2** Fix B1: in `sync_to_d1.py --diff`, compute `since` internally if not provided. Proposed rule: `since = max(run_date in fidelity_transactions WHERE run_date <= today - REFRESH_WINDOW_DAYS) - 1 day`. Or simpler: derive from `sync_meta.last_sync` on the D1 side (query prod's `last_sync`, use that as cutoff). Document the rule.

**P1.3** Delete dead code: `--positions` flag, `verify` mode.

**P1.4** Write `verify_vs_prod.py` that samples random rows from `daily_close`, `computed_daily`, `fidelity_transactions` and compares local ↔ prod. Fail if mismatch beyond float tolerance. Wire into the pre-sync path.

### Phase 2 — Rewrite `run.sh` as Windows-native

**P2.1** Write `pipeline/scripts/run_portal_sync.ps1`:
- Same logic as `run.sh` but in PowerShell
- Change detection includes `Bloomberg.Download*.qfx` (fixes B3)
- Log to `%LOCALAPPDATA%\portal\logs\sync-YYYY-MM-DD.log`
- Exit codes: 0=ok/no-change, 1=build-failed, 2=sync-failed, 3=parity-failed
- Calls `verify_vs_prod.py` before sync

**P2.2** Delete `run.sh` — don't keep two implementations.

**P2.3** Register with Task Scheduler:
```powershell
schtasks /create /tn "PortalSync" /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" /sc daily /st 06:00
```

### Phase 3 — Nice-to-haves

- View migration: add `DROP VIEW IF EXISTS` before each `CREATE VIEW` in `schema.sql`, so re-running `wrangler d1 execute --file=schema.sql` picks up definition changes.
- `sync_meta` consistency: use `_escape()` for `now` / `last_date` (code smell, not bug).
- Consolidate 3 build modes into 2 (`full | incremental`).
- Add `first-run` detection in Windows wrapper: if DB doesn't exist, run `full` automatically.

---

## What NOT to change

- **`daily_close` INSERT OR IGNORE invariant** (PR #98) — works as designed, no complaints from audit.
- **Generated `worker/schema.sql`** — single-source-of-truth pattern is sound.
- **`ingest_fidelity_csv` range-replace** — keeps intra-day duplicate trades (PR #105 revert was correct). The risk is in the *sync* step using range-replace, not the *ingest* step.
- **Fail-open `/timeline` endpoint** — orthogonal, works well.
- **Views doing camelCase alias + shape work in D1** — thin worker is correct architecture.

---

## Files cited

- `pipeline/scripts/sync_to_d1.py:32-60, 66-73, 114-130, 165-177, 181-189`
- `pipeline/scripts/build_timemachine_db.py:92, 434-437, 474-489, 513-518`
- `pipeline/scripts/run.sh:46-72, 97-117`
- `pipeline/etl/db.py:204-273`
- `pipeline/etl/ingest/fidelity_history.py:173-274`
- `pipeline/etl/precompute.py:177-208`
- `worker/schema.sql:111-174`
- `docs/todo-plan-2026-04.md:127` (`verify_vs_prod.py` TODO)
