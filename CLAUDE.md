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
cd worker && npx wrangler dev --remote              # local proxy to remote D1 (port 8787)
cd worker && npx wrangler dev                       # local D1 (seed first with sync --local)

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 34 test files
cd pipeline && .venv/bin/mypy etl/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# Build timemachine DB + sync
cd pipeline && python3 scripts/build_timemachine_db.py
cd pipeline && python3 scripts/sync_to_d1.py        # push to remote D1 (diff, default)
cd pipeline && python3 scripts/sync_to_d1.py --local # push to local D1

# Automated pipeline (Windows, manual run)
powershell -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1

# Dry run (build + verify, no sync)
powershell -ExecutionPolicy Bypass -File pipeline\scripts\run_portal_sync.ps1 -DryRun

# Register with Task Scheduler (daily 06:00)
schtasks /create /tn "PortalSync" /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\Users\guoyu\Projects\portal\pipeline\scripts\run_portal_sync.ps1" /sc daily /st 06:00

# E2E (mock API on port 4444 — no real backend needed)
npx playwright test                                   # 5 Playwright spec files
```

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)

## Type contract

Python `types.py` (snake_case) is source of truth → SQLite `timemachine.db` → D1 views (camelCase aliases) → Worker JSON → Zod `src/lib/schemas/` (camelCase mirror, shared with Worker via `include` in `worker/tsconfig.json`). Keep them in sync. D1 schema is auto-generated from `etl/db.py` via `gen_schema_sql.py`.

## Architecture

Next.js static shell on Cloudflare Pages. Data served by Cloudflare Worker (`worker/src/index.ts`) reading from D1 — same code runs locally via `wrangler dev` and in production. Pipeline (Python) builds `timemachine.db` and syncs to D1.

Frontend fetches all data in a single `GET /timeline` call (~4.6 MB JSON, ~385 KB gzipped by Cloudflare edge), then computes allocation, cashflow, activity, and reconciliation locally in `compute.ts` via `use-bundle.ts`. All daily data points are rendered directly (no downsampling). Brush drag is zero-latency (no network). Ticker charts fetch on-demand via `GET /prices/:symbol`.

D1 schema: 10 base tables (incl. `categories`) + 12 camelCase views (incl. `v_market_meta` pivot, `v_econ_snapshot`, `v_econ_series_grouped`, `v_categories`). Worker serves 3 endpoints: `GET /timeline`, `GET /econ`, `GET /prices/:symbol`. Worker is a thin adapter: `SELECT` → `Zod.safeParse` → JSON. All shape work lives in the views; the only transform in TypeScript is `JSON.parse(sparkline)`, done via a Zod transform shared with the client. All data flows through D1.

`/timeline` is fail-open: the critical `v_daily` query returns 503 on failure, but optional sections (market, holdings, txns) degrade to `null` + a `errors: { market?, holdings?, txns? }` entry. Panels render explicit error cards — missing data never hides silently.

## Accessibility

Chart colors use the Okabe-Ito colorblind-friendly palette (protanomaly-safe): US Equity `#0072B2`, Non-US Equity `#009E73`, Crypto `#E69F00`, Safe Net `#56B4E9`.
