# Portal

## This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing any code. Heed deprecation notices.



Personal finance dashboard: Next.js 16 frontend + Cloudflare Worker/D1, deployed to Cloudflare Pages. Same Worker serves both local dev and production.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static build → out/
npm run test                                        # vitest unit tests

# Backend (Worker — same code for local dev and production)
bash worker/dev-remote.sh                           # --remote through CF Access (sources worker/.env.access)
cd worker && npx wrangler dev                       # local D1 (seed first with sync --local)

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 34 test files
cd pipeline && .venv/bin/mypy etl/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# Build timemachine DB + sync
cd pipeline && python3 scripts/build_timemachine_db.py
cd pipeline && python3 scripts/sync_to_d1.py        # push to remote D1 (diff, default)
cd pipeline && python3 scripts/sync_to_d1.py --local # push to local D1

# Regenerate Zod schemas from etl/types.py (parity-checked in pytest)
cd pipeline && python3 tools/gen_zod.py --write ../src/lib/schemas/_generated.ts

# Regression gate (source-abstraction refactor)
bash pipeline/scripts/regression.sh                 # L1 + L3 regression gate (run before every commit during refactors)
bash pipeline/scripts/regression_baseline.sh        # capture fresh baselines (after approved behavior change)
cd pipeline && .venv/Scripts/python.exe -m pytest tests/regression/test_pipeline_golden.py -v   # L2 fixture-based golden test (CI-friendly)

# Automated pipeline (orchestration in run_automation.py; PS1 is a thin Task Scheduler shim)
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py            # detect-changes → build → verify → sync
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --dry-run   # build + verify, skip sync
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --force --local  # bypass change-detection, push to local D1

# Register with Task Scheduler (daily 06:00) — schtasks still points at the PS1 shim
schtasks /create /tn "PortalSync" /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" /sc daily /st 06:00

# Gmail triage (classifier + Worker + /mail tab)
cd pipeline && .venv/Scripts/python.exe scripts/gmail/triage.py --sync --dry-run  # local dry-run
cd worker-gmail && npx wrangler deploy                # deploy Gmail Worker

# E2E (mock API on port 4444 — no real backend needed)
npx playwright test                                   # 5 Playwright spec files

# Manual Pages deploy (when CI's Worker deploy step is down or when iterating locally)
MSYS_NO_PATHCONV=1 NEXT_PUBLIC_TIMELINE_URL='https://portal.guoyuer.com/api' npx next build
npx wrangler pages deploy out --project-name=portal --commit-dirty=true
```

**Git Bash / MSYS gotcha:** `NEXT_PUBLIC_*=/path npx next build` in Git Bash translates the value into `C:/Program Files/Git/path` before Node sees it (a known MSYS path-conv feature). Prefix any such command with `MSYS_NO_PATHCONV=1` or run from CMD; otherwise the JS bundle bakes in a `file:///C:/…` URL and fetches fail at runtime.

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)

## Type contract

Python `types.py` (snake_case) is source of truth → SQLite `timemachine.db` → D1 views (camelCase aliases) → Worker JSON → Zod `src/lib/schemas/` (camelCase mirror, shared with Worker via `include` in `worker/tsconfig.json`). D1 schema is auto-generated from `etl/db.py` via `gen_schema_sql.py`. Zod schemas for TypedDicts that have a direct projection (`AllocationRow`, `TickerDetail`, `FidelityTxn`, `QianjiTxn`) are auto-generated from `etl/types.py` via `pipeline/tools/gen_zod.py` → `src/lib/schemas/_generated.ts` (`timeline.ts` consumes these via `.omit()` / `.extend()` / re-export); a pytest parity check fails if the committed `_generated.ts` drifts from the Python source. Hand-written schemas remain for shapes without a TypedDict source (market indices, holdings detail, category meta).

The `InvestmentSource` Protocol in `etl/sources/__init__.py` defines `PositionRow` (frozen dataclass: `ticker`, `value_usd`, `quantity`, `cost_basis_usd`, `account`), along with `SourceKind` and `ActionKind` as `StrEnum`s. These are internal to the Python pipeline — they never cross the D1/Worker boundary, so they are not exported to Zod.

## Architecture

Next.js static shell on Cloudflare Pages. Data served by Cloudflare Worker (`worker/src/index.ts`) reading from D1 — same code runs locally via `wrangler dev` and in production. Pipeline (Python) builds `timemachine.db` and syncs to D1.

**Investment sources** live under `pipeline/etl/sources/<name>.py`. Each source (`FidelitySource`, `RobinhoodSource`, `EmpowerSource`) owns its own ingest + `positions_at(as_of, prices)` and is registered in `pipeline/etl/sources/__init__.py::_REGISTRY`. Each source's `__init__(config, db_path)` takes a per-source frozen-dataclass `*SourceConfig`; the central entry point `build_investment_sources(raw, db_path)` iterates the registry via `cls.from_raw_config(raw, db_path)`. `pipeline/etl/replay.py::replay_transactions(db_path, table, as_of) -> dict[str, PositionState]` is the shared, source-agnostic cost-basis primitive — Robinhood fully consumes it; Fidelity still uses `replay_from_db` (different action vocabulary) and may be migrated later. `compute_daily_allocation` is kind-agnostic: it iterates the registry and aggregates `PositionRow` values — no broker-specific code remains. Qianji (cash + spending) stays outside the `InvestmentSource` protocol because of different semantics; market data (Yahoo/FRED) is also outside — they produce series, not positions.

**Adding a new investment source**: create `etl/sources/<name>.py` with `*SourceConfig` + `*Source` + `from_raw_config`, add the `SourceKind.<NAME>` variant, add one line to `_REGISTRY`, and if transaction-level add a `<name>_transactions` table in `etl/db.py`. No changes to `allocation.py` or `compute_daily_allocation`.

Frontend fetches all data in a single `GET /timeline` call (~4.6 MB JSON, ~385 KB gzipped by Cloudflare edge), then computes allocation, cashflow, activity, and reconciliation locally in `compute.ts` via `use-bundle.ts`. All daily data points are rendered directly (no downsampling). Brush drag is zero-latency (no network). Ticker charts fetch on-demand via `GET /prices/:symbol`.

D1 schema: 10 base tables (incl. `categories`) + 12 camelCase views (incl. `v_market_meta` pivot, `v_econ_snapshot`, `v_econ_series_grouped`, `v_categories`). Worker serves 3 endpoints: `GET /timeline`, `GET /econ`, `GET /prices/:symbol`, each wrapped in a CF edge cache (`utils.cachedJson`, 60s/600s/300s TTL). Worker is a thin adapter: `SELECT` → JSON — no runtime Zod validation (the frontend's `use-bundle.ts` parse is the single drift checkpoint; doubling it on the shared schema cost ~200ms CPU per `/timeline` call). All shape work lives in the views; the only TypeScript transform is `JSON.parse(sparkline)`, applied by the client-side Zod.

Gmail triage is a parallel module with its own stack: GitHub Actions cron runs `pipeline/scripts/gmail/triage.py` (IMAP fetch → Claude Haiku classify → POST to `worker-gmail`). `worker-gmail` is a separate Worker with its own D1 (`portal-gmail` → `triaged_emails` table) and a hand-rolled Gmail IMAP client over `cloudflare:sockets` for the trash operation. Route table is split by audience: cron hits `portal-mail.guoyuer.com/mail/sync` (SYNC_SECRET-gated); the browser hits same-origin `portal.guoyuer.com/api/mail/{list,trash}` pre-gated by the same Cloudflare Access app that protects the Pages site — no per-app key. See `docs/gmail-triage-design-2026-04-12.md` (frozen original spec; browser auth superseded by PRs #137-#141).

`/timeline` is fail-open: the critical `v_daily` query returns 503 on failure, but optional sections (market, holdings, txns) degrade to `null` + a `errors: { market?, holdings?, txns? }` entry. Panels render explicit error cards — missing data never hides silently.

## Accessibility

Chart colors use the Okabe-Ito colorblind-friendly palette (protanomaly-safe): US Equity `#0072B2`, Non-US Equity `#009E73`, Crypto `#E69F00`, Safe Net `#56B4E9`.
