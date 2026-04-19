# Portal TODO

**Captured:** 2026-04-18
**Context:** Code quality assessed at ~8.6/10 after the day's 18-PR refactor run. Further polish hits diminishing returns; remaining work is either small real bugs, optional infrastructure upgrades, or product features (a different dimension).

Tiering rule: 🟢 = do if touching this area, 🟡 = do if you expect to keep investing in code quality, 🔵 = defer until a concrete signal, 🔴 = product direction (not refactor).

---

## 🟢 Real bugs / small cleanup (do eagerly)

### T1. Split-validator collapse: same-day split + special dividend

**Where:** `pipeline/etl/prices/validate.py::_validate_splits_against_transactions` (direction 2 path)

**Problem:** Aggregates Fidelity `DISTRIBUTION` rows by `(symbol, run_date)`. A ticker with both a split AND a special dividend on the same day collapses into one row, hiding one event from the cross-check. Surfaced during PR #223 docstring work and noted as latent in that file's "Known limitations" footer. Direction 1 (the `pre_qty × (ratio − 1)` check) handles it; direction 2 (the reverse sanity check) can't disambiguate.

**Fix sketch:** Group by `(symbol, run_date, action_kind)` — splits are `DISTRIBUTION` with `quantity != 0 and price == 0`; cash dividends arrive without `quantity`. OR add a more specific predicate when matching Yahoo split dates.

**Test:** synthetic fixture with VOO 2-for-1 + a $5 special dividend on the same day; assert both events surface.

**Effort:** ~30 min. **Blast radius:** validator only (read-only, zero DB impact).

- [x] Done

### T2. Consolidate `_STATUS_LABELS` / `EXIT_*` constants

**Where:** `pipeline/etl/automation/notify.py` holds `_STATUS_LABELS` with a "Keep in sync with runner.EXIT_*" comment. `pipeline/etl/automation/runner.py` defines `EXIT_OK / EXIT_BUILD_FAIL / ...`. Flagged by the A2 agent as a known duplication driven by circular-import avoidance.

**Fix sketch:** Extract `pipeline/etl/automation/_constants.py` with `EXIT_*` + `_STATUS_LABELS` + any other shared integers. Both `runner.py` and `notify.py` import from it. Cycle broken.

**Test:** existing tests cover both; confirm they still pass.

**Effort:** ~30 min. **Blast radius:** `etl/automation/` internal only.

- [x] Done

---

## 🟡 Infrastructure upgrades (optional, calibrated)

### T3. Property-based tests for replay primitives

**Where:** `pipeline/tests/unit/test_replay_primitive.py`

**Rationale:** `replay_transactions` has real invariants: `BUY.qty > 0`, `SELL.qty < 0`, `cost_basis_usd >= 0`, MM-drip cash equivalence, split+dividend qty conservation, etc. Example-based tests cover specific scenarios; `hypothesis`-generated random transaction sequences would catch edge combinations.

**Do this if:** you expect to keep modifying `replay.py` or add a third source.

**Fix sketch:** Add `hypothesis` to dev deps. One test strategy: generate a sequence of `(date, action, ticker, qty, amount)` tuples, run replay, assert invariants on result.

**Effort:** 1-2 hr. **ROI:** bug-catch proportional to how much you change replay logic in the future.

- [x] Done

### T4. Bundle analyzer + Lighthouse CI baseline

**Where:** `package.json` + new `.github/workflows/frontend-perf.yml` (or integrate into `ci.yml`)

**Rationale:** Frontend is currently ~385 KB gzipped — tight. Purpose is **establish a baseline** so future feature work visibly shows when a new dep inflates it. Same for Lighthouse score.

**Fix sketch:**
- `npm install @next/bundle-analyzer --save-dev`
- `next.config.ts`: wrap with `withBundleAnalyzer({ enabled: process.env.ANALYZE === 'true' })`
- CI step on PRs: `ANALYZE=true npx next build`, save the stats JSON as artifact, optionally comment bundle-diff on PR
- Lighthouse CI via `treosh/lighthouse-ci-action@v12`

**Effort:** 1 hr. **Skip if:** you don't plan to add more frontend features.

- [x] Done

---

## 🔵 Defer until a concrete signal

### T5. Replay checkpoint / incremental state

**Trigger:** when `build_timemachine_db.py` runtime exceeds ~30s, or when `computed_daily` row count > 5k (currently ~2k → build ~10s).

**Shape:** store monthly-end position snapshots in a `replay_checkpoint` table (the one dropped in #208 is fine to resurrect). Replay resumes from the nearest checkpoint instead of re-walking from zero.

**Why deferred:** current build is fast enough. Adding this now is overhead without payoff, and you just deleted the old empty table.

### T6. Cloudflare Logpush → R2

**Trigger:** a prod issue you can't debug with live `wrangler tail`.

**Shape:** Cloudflare dashboard → Worker → Logpush → R2 bucket → 30-day retention. No code change, ~10 min of dashboard clicks.

**Why deferred:** real-time debug covers 95% of cases. Paying for storage + setup time without a concrete need is premature.

### T7. Accessibility audit / keyboard navigation

**Trigger:** someone else starts using this dashboard.

**Why deferred:** you're the sole user and you've already baked in the critical a11y item (protanomaly-safe Okabe-Ito palette for color encodings + paired with shape/letter).

### T8. Mutation testing / fuzzing CSV parsers

**Trigger:** an ingest bug slips past the 643 existing tests.

**Why deferred:** L2 golden + unit + integration coverage is strong; mutation testing has low incremental catch rate for a codebase this size.

---

## 🔴 Product direction (not refactor — a different dimension)

These are **new features**, not code quality. Each requires its own design/impl pass and is worth deciding on product priority before picking one.

### P1. Spending/income trends visualization

**Status:** Qianji sync is live (`qianji_transactions` in D1) but no UI surfaces it. The `/cashflow` route or a new `/spending` tab could chart monthly category-aggregated spending + income.

**Data already there:** `qianji_transactions(date, type, category, amount, is_retirement)`.

**Missing:** D1 view for aggregation, Worker endpoint, frontend chart.

### P2. Monthly savings rate

**Status:** derivable from P1's data plus `fidelity_transactions` + `empower_contributions`.

**Definition:** `(income - spending) / income` per month, or `(contributions + net-of-market-gains) / income` if take-home-based.

**Missing:** derive-or-precompute question, chart component.

### P3. Per-ticker realized P/L (FIFO lot matching)

**Status:** cost basis is tracked in replay as **per-ticker aggregated**, not lot-by-lot. Realized P/L for a SELL would need FIFO lot matching.

**Value:** tax-lot accuracy, realized vs unrealized split, tax-loss harvesting candidate detection.

**Effort:** significant — rework `etl/replay.py::replay_transactions` to track per-lot queue. Likely bigger than any 2026-04-18 PR today.

### P4. Allocation drift alert

**Status:** config.json has `target_weights`; D1 serves current allocation. Comparison is done visually in the UI (allocation chart overlay). No automated alert.

**Fix sketch:** add a threshold check in `run_automation.py` (or a separate cron); email when any category drifts > X% from target for Y consecutive days. Mirror the existing sync-failure email path.

**Effort:** ~1 day.

### P5. Cashflow forecast

**Status:** recurring-bill pattern detection from `qianji_transactions` (same-category same-amount monthly). Project forward 3-6 months.

**Value:** "will I have cash for the mortgage next month" style questions.

**Effort:** non-trivial — pattern detection + projection model + UI.

---

## Explicit non-goals

- Read replicas, horizontal scaling, streaming large payloads — this is a personal dashboard with ~4.6 MB JSON at 385 KB gzipped. Premature.
- Distributed tracing / APM — `wrangler tail` + Cloudflare Analytics suffice.
- Full-scale mutation testing infra — pytest coverage at 94% + L2 golden is enough.
- IaC (Pulumi/Terraform) for Cloudflare resources — one account, dashboard clicks work, CF doesn't drift.
- Microservices / separate codebase per concern — monorepo stays.

---

## Decision log

| Date | Decision | Reason |
|---|---|---|
| 2026-04-18 | Dropped Cloudflare paths-filter CI (#218 closed) | Branch protection requires `python` + `frontend` checks → skipped checks hang required-check gates; the job-level savings marginal for a solo project. |
| 2026-04-18 | Reverted `PORTAL_HEALTHCHECK_URL` B3 enforcement entirely after #227 merge | First attempt hard-failed when unset (broke automation); softened to warn; user rolled further back to pure opt-in (silent if unset). Current state: `ping_healthcheck` no-ops when unset, no startup message either way. |
| 2026-04-18 | Kept `etl/sources/__init__.py` re-exports of types from `_types.py` | Not backcompat — legit public API; underscore on `_types.py` is to avoid circular import only. |
| 2026-04-18 | Kept `ACT_*` constants in `etl/types.py` | DB stores them in `action_type` column; wider vocabulary than `ActionKind`. Not dead. |
