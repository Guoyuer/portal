@AGENTS.md

# Portal

Personal finance dashboard: Next.js frontend + Cloudflare Worker/D1, deployed to Cloudflare Pages. Same Worker serves both local dev and production.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static build → out/

# Backend (Worker — same code for local dev and production)
cd worker && npx wrangler dev --remote              # local proxy to remote D1 (port 8787)
cd worker && npx wrangler dev                       # local D1 (seed first with sync --local)

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # test files
cd pipeline && .venv/bin/mypy generate_asset_snapshot/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# Build timemachine DB + sync
cd pipeline && python3 scripts/build_timemachine_db.py
cd pipeline && python3 scripts/sync_to_d1.py        # push to remote D1
cd pipeline && python3 scripts/sync_to_d1.py --local # push to local D1

# E2E (local only — skipped in CI)
npx next build && npx playwright test               # 4 Playwright spec files
```

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)

## Type contract

Python `types.py` (snake_case) is source of truth → SQLite `timemachine.db` → D1 views (camelCase aliases) → Worker JSON → Zod `schema.ts` (camelCase mirror). Keep them in sync.

## Architecture

Next.js static shell on Cloudflare Pages. Data served by Cloudflare Worker (`worker/src/index.ts`) reading from D1 — same code runs locally via `wrangler dev` and in production. Pipeline (Python) builds `timemachine.db` and syncs to D1.

Frontend fetches all data in a single `GET /timeline` call, then computes allocation, cashflow, activity, and reconciliation locally in `use-bundle.ts`. Brush drag is zero-latency (no network).

R2 (`latest.json`) is legacy and being phased out. The `/econ` page still reads from R2 (`econ.json`).
