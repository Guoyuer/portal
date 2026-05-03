# Structural Cleanup Plan — 2026-04

Audit of file organization, directory structure, and naming conventions. All 11 items below are real issues. This plan commits to paying each one down; it exists so the work can be batched sensibly rather than interleaved with feature PRs.

Scope: post-Batch-3 state (commit `8e44efa`). Only structural changes; no behavior changes.

Effort legend: **XS** (<15 min), **S** (15–60 min), **M** (1–3 hr), **L** (half day+).
Blast legend: file count + rough import-site count.

---

## Status (2026-04-12)

**Executed**:
- ✅ Items **1, 2, 3, 8, 10, 11** — merged in PR #99 (`refactor/structural-quick-wins`)
- ✅ Items **4, 6, 7** — merged in PR #101 (`refactor/frontend-restructure`)
- ✅ Item **5** — merged in PR #100 (`refactor/pipeline-ingest-reorg`)

**Abandoned**:
- ❌ Item **12** (CNY gap manual_rates.csv seed) — superseded by PR #98 invariant protection (\`INSERT OR IGNORE\` for historical rows in `daily_close`). Today's investigation showed Yahoo actually has full CNY=X history (back to 2001-06-25); the "gap" was transient API flakiness, not a permanent data absence. The seed file would have been treating a symptom. The invariant protects the root cause.

**Deferred originally, now also executed**:
- ✅ Item **9** — merged in PR #107 (`refactor/rename-package-etl`). Package renamed `generate_asset_snapshot/` → `etl/`; ~37 Python files + CI / hooks / docs updated in a single PR.

**All items from this audit are now closed.**

---

## 1. `src/lib/__tests__/config.test.ts` inconsistent test location

**Problem**: Frontend test convention in this repo is `xxx.test.ts` co-located with source (see `compute.test.ts`, `format.test.ts`, `use-bundle.test.ts`). But `config.test.ts`, `econ-schema.test.ts`, and `schema.test.ts` sit inside a `src/lib/__tests__/` subdirectory. No other tests use `__tests__/`.

**Current state**:
```
src/lib/__tests__/config.test.ts
src/lib/__tests__/econ-schema.test.ts
src/lib/__tests__/schema.test.ts
src/lib/compute.test.ts          ← co-located (majority convention)
src/lib/format.test.ts           ← co-located
src/lib/use-bundle.test.ts       ← co-located
```

**Proposed**: Move the three `__tests__/` files up one directory, delete the `__tests__` subdirectory.

```
src/lib/config.test.ts
src/lib/econ-schema.test.ts
src/lib/schema.test.ts
```

**Blast**: 3 files moved. No code imports these (tests are discovered by path). `vitest.config.ts` likely has no `testPathIgnorePatterns` that cares — verify before moving.

**Effort**: XS

**Risk**: None. Pure file moves. Run `npm run test` to confirm discovery still works.

---

## 2. Empty `pipeline/tests/{e2e,integration}/` directories

**Problem**: Both directories exist with only `__init__.py` — no actual tests. Either intended for future use (then document) or leftover scaffolding (then delete). Current state signals neither.

**Current state**:
```
pipeline/tests/
├── contract/         ← 1 test file
├── e2e/__init__.py   ← EMPTY
├── integration/__init__.py   ← EMPTY
└── unit/             ← all the actual tests
```

**Proposed**: Delete both empty directories.

```bash
rm -rf pipeline/tests/e2e pipeline/tests/integration
```

If these layers are wanted later (integration against real D1, e2e that exercises the full ingest→sync path), re-create them when the first test exists. Empty scaffolding attracts bit rot.

**Blast**: 2 directories removed.

**Effort**: XS

**Risk**: None.

---

## 3. Screenshot utility scripts scattered at repo root

**Problem**: Screenshot utility scripts had inconsistent placement:

```
screenshot-cashflow.mjs       ← repo root (tracked)
screenshot-market.mjs          ← repo root (tracked)
```

**Proposed**: Move the two `.mjs` files into `scripts/` and keep screenshot
review on the manual Playwright workflow:

```
scripts/screenshot-cashflow.mjs
scripts/screenshot-market.mjs
```

If any `package.json` script references them, update those paths.

**Blast**: 2 files moved + possible `package.json` script path updates.

**Effort**: XS

**Risk**: Low. Verify no CI/tooling reference the old paths.

---

## 4. `src/lib/schema.ts` + `src/lib/econ-schema.ts` split with no clear rule

**Problem**: Zod schemas for API responses live in two flat files:
- `src/lib/schema.ts` — timeline + ticker price schemas
- `src/lib/econ-schema.ts` — econ schemas only

Both are Zod schemas for API responses. The split feels historical, not principled. Adding a 4th endpoint would create pressure for a 3rd file, worsening the ad-hoc structure.

**Current state**:
- `schema.ts`: `TimelineDataSchema`, `TickerPriceResponseSchema`, + nested (Daily, Market, Category, etc.)
- `econ-schema.ts`: `EconDataSchema` + nested

**Proposed**: Convert to a directory:

```
src/lib/schemas/
├── index.ts       ← re-exports all public schemas + types
├── timeline.ts    ← TimelineDataSchema + nested
├── econ.ts        ← EconDataSchema + nested (from econ-schema.ts)
└── ticker.ts      ← TickerPriceResponseSchema
```

Existing imports `from "@/lib/schema"` and `from "@/lib/econ-schema"` all go through `from "@/lib/schemas"` (the index).

**Alternative**: just merge everything into one `src/lib/schema.ts`. Simpler but grows unbounded as API surface grows. Prefer directory.

**Blast**: ~8 importers across `src/` + `worker/` (worker references `src/lib/schema.ts` + `src/lib/econ-schema.ts` directly via relative path — needs update to point at new index or individual files).

**Effort**: S

**Risk**: Low. Pure re-organization. Worker bundle must still resolve — verify `wrangler deploy --dry-run` after.

---

## 5. `db.py` mixes schema DDL with ingestion functions

**Problem**: `pipeline/generate_asset_snapshot/db.py` (~900 LOC now) contains:
- Table DDL (`_TABLES`, `_INDEXES`, `_VIEWS`)
- Connection helpers (`get_connection`, `init_db`)
- Generic DB utilities (`_escape`, `_exec`, etc.)
- **AND** source-specific ingestion functions:
  - `ingest_fidelity_csv`
  - `ingest_empower_qfx`
  - `ingest_empower_contributions`
  - `ingest_qianji_transactions`

The ingestion functions already have a dedicated subpackage (`ingest/`) containing their parsers:
- `ingest/fidelity_history.py`
- `ingest/qianji_db.py`
- `ingest/robinhood_history.py`

But the functions that write parsed records into the DB live in `db.py`. That's a split by "parsing code vs DB-writing code" that serves nobody — each source's ingestion is one coherent unit.

**Proposed**:

1. Move `ingest_fidelity_csv` → `ingest/fidelity_history.py`
2. Move `ingest_empower_qfx` + `ingest_empower_contributions` → `ingest/empower_401k.py` (new file, or merge into existing `empower_401k.py` — currently at package root)
3. Move `ingest_qianji_transactions` → `ingest/qianji_db.py`

After the move, `db.py` shrinks to schema DDL + connection helpers. Each source's ingest module owns its parse + write as one unit.

Consider also renaming `empower_401k.py` → `ingest/empower_401k.py` for consistency.

**Blast**: 4+ files touched. ~6 callers in `scripts/build_timemachine_db.py` need updated imports. Tests (`pipeline/tests/unit/test_db.py`, `test_qianji_db.py`, etc.) may reference these functions — update imports.

**Effort**: M

**Risk**: Medium. Non-trivial refactor. Ensure all call sites + tests updated. Run full `pytest` after.

---

## 6. `src/components/finance/shared.tsx` vague name

**Problem**: "Shared" communicates nothing. The file currently exports:
- 2 constants (`ACTIVITY_TOP_SYMBOLS`, `TOTAL_ROW_CLASS`)
- 4 components (`SectionHeader`, `SectionBody`, `DeviationCell`, `TickerTable`)

The 4 components are not conceptually unified — `SectionHeader/Body` are layout primitives, `DeviationCell` is a formatted cell, `TickerTable` is a domain-specific table.

**Proposed**: Split into three files by concern:

```
src/components/finance/
├── section.tsx         ← SectionHeader + SectionBody (layout)
├── ticker-table.tsx    ← TickerTable + DeviationCell + the 2 constants it uses
```

Constants:
- `ACTIVITY_TOP_SYMBOLS` → used by `TickerTable`, move there
- `TOTAL_ROW_CLASS` → used by `TickerTable` rows, move there

**Blast**: 6 importers (from `grep`): `src/app/finance/page.tsx`, `src/app/econ/page.tsx`, `src/components/finance/cash-flow.tsx`, `src/components/finance/category-summary.tsx`, `src/components/finance/market-context.tsx`, `src/components/finance/net-worth-growth.tsx`. All import from `@/components/finance/shared` — each needs to switch to one of the two new paths (or both).

**Effort**: S

**Risk**: Low. Pure file split.

---

## 7. `src/lib/style-helpers.ts` + `src/lib/chart-styles.ts` overlapping names

**Problem**: Names suggest overlap but contents differ in concern:
- `chart-styles.ts` → Recharts-specific (`tooltipStyle`, `gridStroke`, `axisProps`, `brushColors`)
- `style-helpers.ts` → business thresholds + value coloring (`SAVINGS_RATE_GOOD`, `MAJOR_EXPENSE_THRESHOLD`, `valueColor`, `getIsDark`)

`style-helpers.ts` isn't about styles — it's mostly business thresholds and a dark-mode detector. Misnamed.

**Proposed**: Rename for concern clarity.

```
src/lib/chart-styles.ts   ← keep as-is (recharts-specific)
src/lib/style-helpers.ts  → split:
                              src/lib/thresholds.ts (SAVINGS_RATE_*, MAJOR_EXPENSE_THRESHOLD, SCROLL_SHOW_THRESHOLD, savingsRateColor, valueColor)
                              src/lib/theme.ts       (getIsDark — or merge into hooks.ts with useIsDark which already exists)
```

`getIsDark()` is a non-hook theme check; it pairs naturally with `useIsDark()` in `hooks.ts`. Candidate for merge.

**Blast**: ~5 importers (`chart-styles` consumers + `style-helpers` consumers).

**Effort**: S

**Risk**: Low.

---

## 8. `core/reconcile.py` single-file subdirectory

**Problem**: `pipeline/generate_asset_snapshot/core/` contains exactly one file (`reconcile.py`). Single-file subdirectories create a namespace that carries no organizational value — callers write `from generate_asset_snapshot.core.reconcile import ...`, which is heavier than it needs to be for one module.

**Current state**:
```
pipeline/generate_asset_snapshot/
├── core/
│   ├── __init__.py
│   └── reconcile.py
```

**Proposed**: Flatten. Either:

**Option A (flatten)**: Move `reconcile.py` up one level:
```
pipeline/generate_asset_snapshot/reconcile.py
```

**Option B (populate)**: If `core/` was intended to hold multiple "core domain" modules (e.g., `portfolio.py`, `allocation.py`, `timemachine.py` all belong there conceptually), move those in too. This is a bigger reorganization.

**Recommendation**: Option A. The other "domain" modules (portfolio, allocation, timemachine) are at the package root and form the de-facto "core"; creating a `core/` dir just for them would double the indirection without clear gain.

**Blast**: ~2 importers. Tests (`pipeline/tests/unit/core/test_reconcile.py`) — either keep tests at same structure or flatten.

**Effort**: XS

**Risk**: None. Trivial move + update.

---

## 9. `generate_asset_snapshot/` package name no longer reflects scope

**Problem**: The package was originally named for its first responsibility (generating asset allocation snapshots). It now does:
- Ingestion from Fidelity, Robinhood, Empower 401k, Qianji
- Price fetching from Yahoo, FRED
- Time-series reconstruction via `timemachine.py`
- Precomputation of market indicators, holdings detail, cashflow
- Portfolio reconciliation
- DB schema management + D1 sync

The name is now misleading for new readers — it suggests a narrower scope.

**Proposed**: Rename to something descriptive of the current scope, e.g.:
- `pipeline/portal_pipeline/` (matches project name)
- `pipeline/etl/` (accurate — it IS ETL)
- `pipeline/asset_pipeline/` (domain-specific)

**Recommendation**: `pipeline/etl/` — minimal, truthful, matches established terminology.

**Blast**: LARGE. Every `from generate_asset_snapshot...` import, every script's `sys.path` manipulation, mypy config, test discovery paths, `pyproject.toml` / `setup.cfg` package config. Estimated 30+ file touches.

Tool-assist: `git grep -l "generate_asset_snapshot" | xargs sed -i 's/generate_asset_snapshot/etl/g'` for the bulk rename, then git mv the directory. Still requires careful review.

**Effort**: L

**Risk**: High. Large import blast radius. Do this as its own PR with a focused scope.

---

## 10. Config location split: `data/config.json` vs `pipeline/config.example.json`

**Problem**: Two config files, in two different places, with subtly different roles:

- `data/config.json` — **actual** config (git-ignored, user-local)
- `pipeline/config.example.json` — **template** (git-tracked)

A reader looking for "the config" has to know that:
1. Real config is in `data/` (repo root)
2. Template is in `pipeline/` (pipeline subdir)

The pipeline script has a fallback chain: `args.config || $PORTAL_CONFIG || data/config.json`. So the top-level `data/config.json` is the default.

**Proposed**: Co-locate real config and template, in `pipeline/` (where the consuming code lives):

```
pipeline/config.json         ← real config (git-ignored)
pipeline/config.example.json ← template (git-tracked)
```

Update `build_timemachine_db.py`'s default path + any other readers. Update `.gitignore` (add `pipeline/config.json` if not already excluded; may already be covered).

**Blast**: 1 path constant in `build_timemachine_db.py` + documentation references (CLAUDE.md, README). Real move: `mv data/config.json pipeline/config.json` — user-local, not tracked.

**Effort**: XS-S (depending on doc sweep)

**Risk**: Low. Config path is derived from an env var fallback chain; reassigning the default is one line.

---

## 11. `worker/schema.sql` is a generated artifact in the consumer package

**Problem**: `worker/schema.sql` is auto-generated by `pipeline/scripts/gen_schema_sql.py` from Python sources (`db.py`'s `_TABLES`, `_INDEXES`, `_VIEWS`). The generator's output lands in `worker/`, not in `pipeline/`. This makes the source-of-truth unclear:

- Source: `pipeline/generate_asset_snapshot/db.py` (Python)
- Artifact: `worker/schema.sql` (checked in, consumed by wrangler)

A reader who opens `worker/schema.sql` to understand the schema sees stale comments / hand-editable-looking SQL, then has to discover that it's actually generated.

**Proposed — Option A (leave as-is + improve signaling)**:
- Keep file location (wrangler needs `worker/schema.sql` for `wrangler d1 execute --file=...` commands — moving it adds a relative path to the command)
- Strengthen the header comment: `-- GENERATED FILE — DO NOT EDIT. Source: pipeline/generate_asset_snapshot/db.py. Regenerate: cd pipeline && python3 scripts/gen_schema_sql.py`
- Add a CI check that fails if `schema.sql` drifts from `gen_schema_sql.py` output.

**Proposed — Option B (move source of truth)**:
- Keep `schema.sql` at `worker/schema.sql` (wrangler needs it there)
- But make the Python side pure — generator writes to `worker/schema.sql` from a template

(Identical result to current state; just documenting that option is identical.)

**Recommendation**: Option A. The file location is correct (wrangler needs it); the issue is pure signaling. A drift-detection CI check is the real fix — prevents silent artifact drift when someone forgets to regenerate.

**Blast**: 1 comment change in `gen_schema_sql.py` output header; 1 CI workflow file.

**Effort**: XS (comment) + S (CI check, if wanted)

**Risk**: None.

---

## 12. CNY gap: rebuilding the DB fails on fresh checkout

**Problem**: `build_timemachine_db.py full` fails with:
```
ValueError: No CNY rate available at or before 2023-03-13 — daily_close is missing CNY=X data
```

Yahoo Finance's `CNY=X` history starts at 2023-07-05, but the earliest Fidelity transaction is 2023-03-13, leaving an 82-day gap (2023-03-13 → 2023-07-04). The USD/CNY exchange rate is needed to convert CNY-denominated assets (Alipay Funds, Managed Fund, Bank Card, etc. — see `config.json::qianji_accounts.cny`) into the USD-denominated net worth series.

Historically, the gap was filled **manually** in the local `timemachine.db` (checking archived `data/timemachine_old.db` confirms 82 rows of `(symbol='CNY=X', date∈[2023-03-13..2023-07-04])` present there). That manual fill was never promoted to a repo-tracked source of truth, so any fresh rebuild (new contributor, CI, delete + rebuild) immediately fails.

This violates an invariant that should hold: **rebuilding the DB from raw inputs + repo-tracked seeds must always succeed**.

**Current state**:
- `fetch_and_store_cny_rates()` hits Yahoo Finance → misses the 82-day window → `daily_close` has no rows for that range
- `compute_daily_allocation()` → `_resolve_date_windows()` raises on the first day with no CNY rate
- The user's current remote D1 has `computed_daily` locked in from a prior successful build (that used the manual fill) — so prod is safe, but regeneration is broken

**Proposed**: Promote the manual backfill to a repo-tracked seed file, merged into `daily_close` during `fetch_and_store_cny_rates`.

1. **New seed file**: `pipeline/data/manual_rates.csv` (tracked in git)
   ```csv
   symbol,date,close
   CNY=X,2023-03-13,6.9052
   CNY=X,2023-03-14,6.8890
   ...
   CNY=X,2023-07-04,7.2398
   ```
   Generate the initial content by extracting those 82 rows from the archived `data/timemachine_old.db` — a one-off setup commit. Small CSV (~3 KB), trivially diffable, easy to extend if other gaps appear (e.g., other FX pairs, bond proxy prices).

2. **Merge in pipeline**: Modify `pipeline/generate_asset_snapshot/prices.py::fetch_and_store_cny_rates` (and eventually `fetch_and_store_prices` if we want general coverage) to:
   - Fetch from Yahoo as today
   - Load `manual_rates.csv`
   - `INSERT OR IGNORE` manual rows after Yahoo rows (so Yahoo wins on overlapping dates, manual fills gaps)
   - Log the count of manual rows applied for observability

3. **Generalize (optional, if we want)**: `manual_rates.csv` could cover any `(symbol, date, close)` — not just CNY. Same mechanism would handle, e.g., pre-listing gaps for mutual funds or delisted tickers.

4. **Validation**: Add a test / assertion that after `fetch_and_store_cny_rates(earliest_fidelity_date, end)`, every day in the range has a row in `daily_close WHERE symbol='CNY=X'`. Fast, cheap guard that catches any new gap.

5. **Documentation**: One paragraph in CLAUDE.md explaining the seed file's purpose and when to extend it.

**Blast**: 1 new CSV (~82 lines + header) + 1 modified function in `prices.py` + 1 new test. No changes to schema.

**Effort**: S

**Risk**: Low. Pure additive — if `manual_rates.csv` is empty, behavior is identical to today. The new `INSERT OR IGNORE` cannot overwrite Yahoo data (the default is that Yahoo wins; manual is a gap-filler).

**Why this is structural, not ad-hoc**: The current state has load-bearing data living **only in a single developer's local SQLite file**. That's a silent bus-factor-1 dependency on one person's filesystem. Promoting it to a tracked repo artifact is the kind of hygiene that this plan exists to enforce.

---

## Prioritization & execution plan

### By effort + risk

| # | Title | Effort | Risk | Priority |
|---|-------|--------|------|----------|
| 12 | CNY gap seed file (rebuild-ability) | S | Low | **High — integrity** |
| 1 | Test location consistency | XS | None | Quick win |
| 2 | Delete empty test dirs | XS | None | Quick win |
| 3 | Screenshot scripts to `scripts/` | XS | None | Quick win |
| 8 | Flatten `core/reconcile.py` | XS | None | Quick win |
| 11 | `schema.sql` header + drift CI | XS–S | None | Quick win |
| 10 | Config co-location | XS–S | Low | Quick win |
| 4 | Schemas subdirectory | S | Low | Medium |
| 6 | Split `shared.tsx` | S | Low | Medium |
| 7 | Rename `style-helpers.ts` | S | Low | Medium |
| 5 | Move `ingest_*` out of `db.py` | M | Medium | Medium |
| 9 | Rename `generate_asset_snapshot/` | L | High | Its own PR |

Item 12 is flagged as High-priority integrity work: without it, the codebase claims to be rebuildable but isn't. Every other cleanup item is pure polish on a codebase that still regenerates cleanly from raw inputs + repo seeds — item 12 restores that baseline guarantee.

### Recommended PR grouping

**PR A — Quick wins (all XS, zero risk)**
Items 1, 2, 3, 8, 10, 11 (header change only).
One commit per item; single PR. ~1 hour of work.

**PR B — Frontend restructure**
Items 4, 6, 7. Related (all frontend file moves/renames). One commit per item; single PR.

**PR C — Pipeline ingest reorganization + CNY seed**
Items 5, 12. Both touch the pipeline data flow; do together so tests can verify rebuild-from-scratch works cleanly.

**PR D — Package rename**
Item 9 standalone. Largest blast radius — deserves isolation for easy revert if something breaks.

**PR E (optional) — schema.sql drift CI**
Item 11 CI check portion. Can be added to any existing CI workflow PR.

### Ordering

Recommended:
1. **PR A** first (cheap; no dependencies)
2. **PR B** (frontend; independent of pipeline)
3. **PR C** (pipeline ingest; not blocked by others)
4. **PR D** last (big blast; do after everything else stable so revert is easy)

PR A/B/C could run in parallel via separate worktrees — no file overlap between frontend (B) and pipeline (C); A touches both but only trivially.

---

## Non-goals

- No behavior changes. Every item in this plan should be observably a no-op (tests pass identically before and after).
- No new features. Reorganizing module boundaries for future features is out of scope unless the current state blocks the next planned change.
- No CLAUDE.md/README updates beyond what's needed for renamed paths.
