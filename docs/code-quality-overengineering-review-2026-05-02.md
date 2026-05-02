# Code Quality and Overengineering Review - 2026-05-02

Status: Historical review. This document was written against the pre-R2/D1
architecture and should not be used as current implementation guidance. Use
`docs/post-r2-architecture-cleanup-2026-05-02.md` for the active cleanup plan.

Scope: current `main` after PR #275 (`bfe06f7`). This review records defects still present on
the branch plus overengineering patterns that look like "vibe coding" residue: useful-looking
process, tests, or abstractions that now cost more than they protect.

This supersedes stale portions of `docs/archive/code-quality-review-2026-04.md`. Many April
findings were fixed by later refactors, so they are not repeated here.

## Follow-up Status

Resolved on 2026-05-02:

- R1/R2: sync replacement policy is centralized in `pipeline/scripts/sync_policy.py`; `verify_vs_prod.py`
  now checks every synced table according to the same destructive-boundary policy as `sync_to_d1.py`.
- R3/R4: CI now includes Worker paths and Worker typecheck; ESLint is pinned to a compatible 9.x major
  and `npm run lint` runs cleanly.
- R5/O-cleanup: the brush performance diagnostic moved to `e2e/manual/`; stale no-op e2e checks were
  removed instead of kept as ceremonial coverage.

Follow-up verification:

- `npm run lint`: passed with 0 warnings.
- `npm run test`: 282 tests passed.
- `npm run test:coverage`: 282 tests passed, coverage above thresholds.
- `npm run build`: Next static export passed.
- `npx tsc --noEmit`: frontend typecheck passed.
- `npx tsc -p worker/tsconfig.json --noEmit`: Worker typecheck passed.
- `npx playwright test`: 52 passed, 1 intentionally skipped.
- `cd pipeline && .venv/Scripts/python.exe -m pytest -q --basetemp %TEMP%/...`: 677 tests passed.
- `cd pipeline && .venv/Scripts/python.exe -m mypy etl/ --strict --ignore-missing-imports`: passed.
- `cd pipeline && .venv/Scripts/python.exe -m ruff check .`: passed.

## Original Verification Run

Passed:

- `npm run test`: 282 tests passed.
- `npm run test:coverage`: 282 tests passed, coverage above thresholds.
- `npm run build`: Next static export passed.
- `npx tsc --noEmit`: frontend typecheck passed.
- `npx tsc -p worker/tsconfig.json --noEmit`: Worker typecheck passed.
- `cd pipeline && .venv/Scripts/python.exe -m pytest -q --basetemp %TEMP%/...`: 673 tests passed.
- `cd pipeline && .venv/Scripts/python.exe -m mypy etl/ --strict --ignore-missing-imports`: passed.
- `cd pipeline && .venv/Scripts/python.exe -m ruff check .`: passed.

Failed / degraded:

- `npm run lint` fails before linting code. `eslint@10.2.1` is incompatible with the current
  Next/React plugin chain; the crash happens while loading `react/display-name`.
- `npx playwright test` fails because `e2e/perf-brush.spec.ts` is included in the default suite:
  the dev-server case hardcodes `localhost:3000`, and the production-build case measured
  `p95=219ms` against a `<50ms` threshold on this machine.
- A failed/interrupted pytest run left `pipeline/.pytest-tmp-review` unreadable on this Windows
  checkout. This is a local artifact, not a repo defect, but it currently makes `git status`
  print a permission warning.

## Defects / Risk Findings

### R1. Pre-sync data-loss gate checks fewer tables than sync can replace

Severity: high.

`pipeline/scripts/verify_vs_prod.py` checks row counts for only:

```python
_TABLES_FOR_COUNT = ["fidelity_transactions", "qianji_transactions", "computed_daily", "daily_close"]
```

But `pipeline/scripts/sync_to_d1.py::TABLES_TO_SYNC` syncs more tables:

- `computed_daily_tickers`
- `robinhood_transactions`
- `empower_contributions`
- `computed_market_indices`
- `computed_holdings_detail`
- `econ_series`
- `categories`

Several of those are range-replaced or full-replaced in diff mode. If local loses rows in one of
those tables, the gate can pass and the sync can still delete prod data.

Recommendation: make `verify_vs_prod` derive its protection list from the same sync policy used by
`sync_to_d1.py`:

- DIFF tables (`INSERT OR IGNORE`) can allow `local < prod`.
- RANGE and FULL tables should fail on unexplained `local < prod`.
- `--expected-drops` should apply to every destructive table, not just the four current ones.

### R2. `computed_daily` drift check covers 7 rows, but sync rewrites about 60 days

Severity: high.

`verify_vs_prod.py` compares:

```sql
SELECT date, total FROM computed_daily ORDER BY date DESC LIMIT 7
```

`sync_to_d1.py` default diff mode derives `--since` as latest Fidelity `run_date - 60 days`, then
range-replaces `computed_daily` and `computed_daily_tickers` after that cutoff.

Result: drift in the 8-to-60-day window can avoid the gate and still be written to prod.

Recommendation: compute the same `since` in `verify_vs_prod` and compare every overlapping
`computed_daily` row that the sync will replace. The allowed recent-drift boundary should come from
`etl.prices.refresh_window_start()` rather than a separate `_RECENT_WINDOW_DAYS = 7` constant.

### R3. Worker-only PRs can skip CI coverage

Severity: medium.

`vitest.config.ts` includes `worker/src/**/*.test.ts`, but `.github/workflows/ci.yml` path filters
do not include `worker/src/**`, `worker/package.json`, `worker/package-lock.json`, or
`worker/tsconfig.json` in the frontend job. A PR that only changes Worker code can no-op both the
frontend job and the Python job, while branch protection still reports success.

Recommendation: either add a dedicated `worker` job, or include Worker paths in the existing
frontend filter and run:

- `npm run test:coverage`
- `npx tsc -p worker/tsconfig.json --noEmit`

### R4. JS lint command is broken

Severity: medium.

`package.json` pins `eslint-config-next@16.2.4` but allows `eslint@^10.2.1`. The installed tree
shows invalid peer ranges from React/Next lint plugins, and `npm run lint` crashes before reporting
any lint findings.

Recommendation: pin ESLint to a compatible major, likely ESLint 9 for the current plugin chain, or
drop `npm run lint` from advertised local checks until the Next/ESLint stack supports 10 cleanly.
Then wire lint into CI if it is meant to matter.

### R5. Perf Playwright spec is in the default suite but is not a stable regression test

Severity: low to medium.

`e2e/perf-brush.spec.ts` says it requires a local dev server and backend, but default
`playwright.config.ts` does not start `localhost:3000` and does not ignore this file. The production
case asserts a strict p95 frame-time threshold that is highly machine-dependent.

Recommendation: move it to `e2e/manual/` or a dedicated manual Playwright config. Keep the output as
a diagnostic tool, not as part of default regression.

## Vibe-Coding Overengineering Candidates

These are not all bugs. They are places where the repo appears to have accumulated ceremony or
abstraction faster than the actual solo-dashboard use case needs.

### O1. Planning docs are much larger than the features they describe

Examples:

- `docs/plans/2026-04-20-investment-activity.md`: 1,358 lines.
- `docs/plans/2026-04-19-group-aggregated-bs-markers.md`: 1,016 lines.
- `docs/plans/2026-05-01-distinguish-parity-infra-vs-drift.md`: 498 lines.

This is useful as an audit trail, but it is not useful as active operating documentation. The
current live docs (`README.md`, `ARCHITECTURE.md`, `RUNBOOK.md`, `TODO.md`) are enough for day-to-day
work.

Recommendation: keep large plan docs archived, but do not keep producing 500-1,300 line plans for
small personal-dashboard changes. For new work, use a short design note only when there is a real
cross-module contract.

### O2. CI path filtering adds complexity and already caused a blind spot

The `detect` job exists to avoid running expensive jobs, but this repo's core checks are not that
expensive locally:

- Python pytest: about 26 seconds on this machine when basetemp is healthy.
- Vitest: about 4 seconds.
- Next build: about 8 seconds.

The path filter now has to understand Python, frontend, Worker, schema, e2e, and deploy coupling.
It currently misses Worker code.

Recommendation: strongly consider removing path filters for PRs and always running the core checks.
If branch protection requires stable status checks anyway, the simpler always-run setup may be
cheaper than maintaining skip logic.

### O3. Sync policy is duplicated across scripts, comments, tests, and docs

`sync_to_d1.py` owns the actual write semantics. `verify_vs_prod.py` re-states a partial model of
those semantics. The new R1/R2 bugs come from that duplication.

Recommendation: create one explicit sync-policy data structure and import it from both sync and
verification code. The policy should answer:

- table name
- mode: diff, range, or full
- date expression for range mode
- whether `local < prod` is allowed
- how much drift is tolerated and for what window

This is a simplification, not another abstraction layer, because it deletes a second hand-maintained
model of the same system.

### O4. Some e2e tests are conditional enough to become smoke tests with weak signal

`e2e/finance.spec.ts` contains many branches like `return` when data is missing, or checks that only
run if an optional element happens to exist. A few examples:

- cashflow table absent: return
- activity table absent: return
- market card absent: return
- timemachine section absent: skip
- tests with only a comment body after a feature was removed

For a mock API suite, this is usually over-defensive. The fixture is controlled, so the test should
assert exact expected fixture state or be deleted.

Recommendation: split the suite into:

- deterministic mock-fixture regression specs with no silent returns
- one or two smoke specs that explicitly check fail-open behavior
- manual visual/perf specs under `e2e/manual/`

### O5. Automation email/changelog stack may be more detailed than the operational need

The automation stack is robust, but large for a personal repo:

- `pipeline/etl/automation/runner.py`: 293 lines.
- `pipeline/etl/automation/notify.py`: 222 lines.
- `pipeline/etl/changelog/snapshot.py`: 502 lines.
- `pipeline/tests/unit/test_changelog.py`: 832 lines.
- `pipeline/tests/unit/test_run_automation.py`: 765 lines.

Some of this is real safety: this pipeline syncs financial history to D1, so failure emails and
auditability matter. The overengineering risk is expanding the email report and status taxonomy
after the useful alerts already exist.

Recommendation: freeze this surface unless a real operational failure exposes a missing signal.
Avoid adding more categories, templates, or status labels. Prefer fewer, sharper alerts:

- build failed
- parity drift
- parity infra failure
- positions check failed
- sync failed
- sync succeeded

### O6. The repo has a test count bias toward proving implementation details

The Python suite is strong and mostly justified, but the largest files show the risk:

- `test_changelog.py`: 832 lines.
- `test_run_automation.py`: 765 lines.
- `test_prices.py`: 628 lines.
- `test_allocation.py`: 556 lines.

This is not automatically bad. The pipeline has real data-loss risk. The warning sign is when tests
lock in intermediate formatting, email wording, or private helper behavior while missing higher-level
contract gaps like R1/R2.

Recommendation: do not add more unit tests around implementation shape unless they replace a brittle
manual process. Prefer contract tests around destructive boundaries:

- what rows a sync can delete
- what the pre-sync gate must block
- Worker schema drift versus frontend Zod
- one fixture-level golden path

## Not Overengineering

These areas look complex but currently pay rent:

- The investment-source protocol and replay primitive: needed to keep Fidelity, Robinhood, and
  Empower out of allocation logic.
- Python-to-Zod generation: prevents type-contract drift across the D1/Worker/frontend boundary.
- Client-side Zod validation for `/timeline`: valuable because the Worker intentionally stays thin.
- Worker fail-open for optional sections: improves availability and already has tests.
- L1/L2 regression baselines: justified for a finance pipeline where silent historical drift is worse
  than CI cost.

## Recommended Cleanup Order

1. Fix R1 and R2 first. These are correctness issues at the destructive sync boundary.
2. Fix R3 and R4 so CI reflects the code that changed and lint is either real or removed.
3. Move `e2e/perf-brush.spec.ts` out of the default suite.
4. Simplify CI path filtering, or at minimum include Worker paths.
5. Prune/discipline future planning docs. Keep large historical plans archived; use concise design
   notes for new work.
6. Tighten e2e tests by removing silent returns where the mock fixture should be deterministic.
