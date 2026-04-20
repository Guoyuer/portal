# Portal

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.

Personal finance dashboard: Next.js 16 frontend (static export) + Cloudflare Worker/D1, deployed to Cloudflare Pages. Same Worker binary serves both local dev and production.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static export → out/
npm run test                                        # vitest run (27 test files)

# Backend (Worker — same code for local dev and production)
bash worker/dev-remote.sh                           # --remote through CF Access (sources worker/.env.access)
cd worker && npx wrangler dev                       # local D1 (seed first with sync --local)

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 42 test files (665 tests)
cd pipeline && .venv/bin/mypy etl/ --strict --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# Build timemachine DB + sync
cd pipeline && python3 scripts/build_timemachine_db.py
cd pipeline && python3 scripts/sync_to_d1.py        # push to remote D1 (diff, default)
cd pipeline && python3 scripts/sync_to_d1.py --local # push to local D1 (implies --full)

# Regenerate Zod schemas from etl/types.py (parity-checked in pytest)
cd pipeline && python3 tools/gen_zod.py --write ../src/lib/schemas/_generated.ts

# Regression gate (fixture-driven — see docs/RUNBOOK.md §6)
cd pipeline && .venv/Scripts/python.exe -m pytest tests/regression/ -v              # L1 row-level + L2 fixture-based golden (CI-friendly, fully offline)
cd pipeline && .venv/Scripts/python.exe scripts/refresh_l1_baseline_from_fixtures.py  # refresh L1 baselines from fixtures locally
# Attach `baseline-refresh` label to a PR → CI runs the fixture refresh + pushes the new .sha256 back (see docs/RUNBOOK.md §6).

# Automated pipeline (orchestration in run_automation.py; PS1 is a thin Task Scheduler shim)
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py            # detect-changes → build → verify → sync
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --dry-run   # build + verify, skip sync
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --force --local  # bypass change-detection, push to local D1

# Register with Task Scheduler (at logon, +2min delay for network to settle — laptop is asleep at fixed daily times)
# Use PowerShell's Register-ScheduledTask; schtasks.exe's /delay flag needs admin, Register-ScheduledTask doesn't.
powershell -NoProfile -Command "$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1'; $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME; $trigger.Delay = 'PT2M'; Register-ScheduledTask -TaskName 'PortalSync' -Action $action -Trigger $trigger"

# E2E (mock API on port 4444 — no real backend needed)
npx playwright test                                   # 7 specs under e2e/ + 2 under e2e/manual/ (main config excludes manual)
npx playwright test -c playwright.manual.config.ts    # manual-only debug specs (excluded from CI)
npx playwright test -c playwright.config.real.ts      # hit real Worker on portal.guoyuer.com (used by e2e-real-worker workflow)

# Manual Pages deploy (when CI's Worker deploy step is down or when iterating locally)
MSYS_NO_PATHCONV=1 NEXT_PUBLIC_TIMELINE_URL='https://portal.guoyuer.com/api' npx next build
npx wrangler pages deploy out --project-name=portal --commit-dirty=true
```

**Git Bash / MSYS gotcha:** `NEXT_PUBLIC_*=/path npx next build` in Git Bash translates the value into `C:/Program Files/Git/path` before Node sees it (a known MSYS path-conv feature). Prefix any such command with `MSYS_NO_PATHCONV=1` or run from CMD; otherwise the JS bundle bakes in a `file:///C:/…` URL and fetches fail at runtime.

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)
- React Compiler is enabled project-wide — DO NOT add manual `useMemo` / `useCallback`. Move expensive transforms upstream into `src/lib/compute/compute.ts` or `src/lib/format/` instead.

## Dev tooling

- `.mcp.json` (committed) declares two MCP servers Claude Code picks up in this workspace: `chrome-devtools` (live browser driving for visual QA + devtools) and `playwright` (scripted browser automation). Both are launched via `cmd /c npx` — Windows-only invocation; if you clone on Mac/Linux, replace `cmd /c` with a direct `npx` call locally.
- Manual visual-QA scripts live under `scripts/screenshot-*.{mjs,js}`. They are not wired into CI or `package.json` scripts — invoke them directly with `node`.

## Type contract

Python `etl/types.py` (snake_case TypedDicts) is source of truth → SQLite `timemachine.db` → D1 views (camelCase aliases) → Worker JSON → Zod `src/lib/schemas/` (camelCase mirror, shared with Worker via `include` in `worker/tsconfig.json`).

D1 schema is auto-generated from `etl/db.py` via `gen_schema_sql.py` at sync time. Zod schemas for TypedDicts that have a direct view projection (`AllocationRow`, `TickerDetail`, `FidelityTxn`, `QianjiTxn`, `RobinhoodTxn`, `EmpowerContribution`) are auto-generated from `etl/types.py` via `pipeline/tools/gen_zod.py` → `src/lib/schemas/_generated.ts` (`timeline.ts` consumes these via `.omit()` / `.extend()` / re-export); a pytest parity check fails if the committed `_generated.ts` drifts from the Python source. Hand-written schemas remain for shapes without a TypedDict source (market indices, holdings detail, category meta).

**Pipeline-internal types** (never crossing the D1/Worker boundary, so NOT in Zod) live in `pipeline/etl/sources/_types.py`:

- `ActionKind` — `StrEnum` normalizing broker action strings (`buy`/`sell`/`reinvestment`/`distribution`/…).
- `PriceContext` — frozen dataclass passed to `InvestmentSource.positions_at` carrying `as_of`, `prices: dict[str, float]`, `config`.
- `PositionRow` — frozen dataclass (`ticker`, `value_usd`, `quantity`, `cost_basis_usd`, `account`).
- `InvestmentSource` — the `Protocol` all investment-source modules implement.

`etl/sources/__init__.py` re-exports these + enumerates the concrete sources via a lazy `_sources()` loader (avoids circular imports between the Protocol module and concrete implementations).

### Fidelity lot semantics (heads-up)

`fidelity_transactions.lot_type` holds Fidelity's account-bucket label (`Cash` / `Margin` / `Shares` / `Financing` / `""`) — NOT tax-lot identifiers. Lot-level cost-basis tracking / wash-sale / tax-loss harvesting would require a separate realized-G/L data source that is currently not ingested.

## Architecture

Next.js static shell on Cloudflare Pages (`output: "export"`). Data served by a single Cloudflare Worker (`worker/src/index.ts`, deployed as `portal-api`) reading from D1 — same code runs locally via `wrangler dev` and in production as a zone route on `portal.guoyuer.com/api/*`. Pipeline (Python) builds `timemachine.db` locally and syncs to D1 via `scripts/sync_to_d1.py` (diffs by default; `--full` wipes + restores; `--local` implies `--full`).

### Investment sources

Investment sources live under `pipeline/etl/sources/`:

- `fidelity/` — directory module (parse.py, pricing.py, cash.py, __init__.py) because Fidelity has CSV parsing + closing-price fallback + cash balance reconstruction.
- `robinhood.py`, `empower.py` — single-file sources.
- `_types.py` — the Protocol + shared dataclasses (above).
- `_ingest.py` — shared range-replace idempotency helper used by multiple sources.

Each source exposes `ingest(db_path, config)`, `positions_at(db_path, ctx) -> list[PositionRow]`, and `produces_positions(config) -> bool`. The `SOURCES` list in `etl/sources/__init__.py` enumerates them in iteration order (Fidelity, Robinhood, Empower). `compute_daily_allocation` and `positions_at_all` iterate `SOURCES` and aggregate `PositionRow` values — no broker-specific code remains in allocation.

`pipeline/etl/replay.py::replay_transactions(db_path, config, as_of) -> ReplayResult` is the shared source-agnostic cost-basis primitive — Fidelity and Robinhood both consume it via module-level `FIDELITY_REPLAY` / `ROBINHOOD_REPLAY` `ReplayConfig` instances.

Qianji (cash + spending) stays outside the `InvestmentSource` Protocol because of different semantics (categorical flows, not positions). Market data (Yahoo/FRED) is also outside — it produces time-series, not positions.

**Adding a new investment source**: create `etl/sources/<name>.py` (or directory) with module-level `ingest` / `positions_at` / `produces_positions` free functions, add it to the `SOURCES` list in `etl/sources/__init__.py::_sources()`, and if transaction-level add a `<name>_transactions` table in `etl/db.py` + a module-level `ReplayConfig` for the shared primitive. No changes to `allocation.py` or `compute_daily_allocation`.

### Frontend data flow

Frontend fetches all data in a single `GET /timeline` call (~4.6 MB JSON, ~385 KB gzipped by Cloudflare edge), then computes allocation, cashflow, activity, reconciliation, and grouped activity locally in `src/lib/compute/compute.ts` (entered through `compute-bundle.ts`) via `src/lib/hooks/use-bundle.ts`. All daily data points are rendered directly (no downsampling). Brush drag is zero-latency (no network). Ticker charts fetch on-demand via `GET /prices/:symbol`.

Key compute outputs (all pure functions of the parsed `/timeline` bundle):

- `computeCategories`, `computeSnapshot` — allocation donut + totals.
- `computeMonthlyFlows` — monthly income/expense/savings/savingsRate (months with zero income are dropped upstream).
- `computeActivity` / `computeGroupedActivity` — buys/sells/dividends per ticker or equivalence group.
- `computeCrossCheck` — Fidelity + Robinhood deposit reconciliation (bipartite matching vs Qianji transfers, earliest-in-window).

Hooks layering — `use-bundle.ts` is a thin orchestrator that composes three focused units: `use-timeline-data.ts` (fetch + Zod safeParse — the single drift checkpoint), `use-brush-range.ts` (brush window state + 1-year default + reset-on-data effect), and the pure `compute-bundle.ts` builder. `use-hover-state.ts` owns the marker-hover state reused across ticker + group dialogs.

### Equivalent-groups (S&P 500, NASDAQ 100)

`src/lib/config/equivalent-groups.ts` declares hand-maintained `EQUIVALENT_GROUPS` mapping a display name to a list of member tickers plus a `representative` ticker. Members must be disjoint across groups; a module-load invariant check throws on violation.

Consumers:

- `computeGroupedActivity` folds buys/sells/dividends for members into one row per group.
- `group-aggregation.ts::groupNetByDate` clusters Fidelity REAL transactions by T+2 window and emits net exposure change per cluster — noise swaps below a $50 threshold are dropped.
- `group-chart.tsx` plots the representative ticker's `GET /prices/:symbol` as the Y-axis, overlaying net buy/sell markers. This makes rebalance timing visible even when members are swapped.

### D1 schema

**13 base tables** (generated from `etl/db.py` via `gen_schema_sql.py`) plus `sync_meta` + `sync_log` (appended by the sync tooling) = **15 total tables** at the D1 end.

Base tables:

- `categories`, `computed_daily`, `computed_daily_tickers`, `computed_holdings_detail`, `computed_market_indices`
- `daily_close` (price history cache)
- `econ_series`
- `empower_snapshots`, `empower_funds`, `empower_contributions`
- `fidelity_transactions`, `robinhood_transactions`, `qianji_transactions`

**12 camelCase views** (shape layer — Worker does zero row mutation):

`v_daily`, `v_daily_tickers`, `v_fidelity_txns`, `v_qianji_txns`, `v_robinhood_txns`, `v_empower_contributions`, `v_categories`, `v_market_indices`, `v_holdings_detail`, `v_econ_series`, `v_econ_series_grouped`, `v_econ_snapshot`.

### Worker

Serves 3 endpoints: `GET /timeline`, `GET /econ`, `GET /prices/:symbol`, each wrapped in a CF edge cache (`utils.cachedJson`, 60s/600s/300s TTL). Routes strip an optional `/api` prefix so the same code serves both `workers.dev` and the production zone route on `portal.guoyuer.com/api/*`.

Worker is a thin adapter: `SELECT` → JSON — no runtime Zod validation (the frontend's `use-timeline-data.ts` Zod `safeParse` — composed by `use-bundle.ts` — is the single drift checkpoint; doubling it on the shared schema cost ~200ms CPU per `/timeline` call). All shape work lives in the views; the only TypeScript transform is `JSON.parse(sparkline)`, applied by the client-side Zod.

`/timeline` is fail-open: the critical `v_daily` query returns 503 on failure, but optional sections (market, holdings, txns) degrade to `null` + a `errors: { market?, holdings?, txns? }` entry. Panels render explicit error cards — missing data never hides silently.

## Accessibility

Chart colors use the Okabe-Ito colorblind-friendly palette (protanomaly-safe): US Equity `#0072B2`, Non-US Equity `#009E73`, Crypto `#E69F00`, Safe Net `#56B4E9`. Categorical encodings are always paired with a letter/shape (B/S markers on trade clusters, `FID`/`RH`/`401k` badges on source tags) — color alone is insufficient.
