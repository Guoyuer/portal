@AGENTS.md

# Portal

Personal finance dashboard: Next.js frontend + Cloudflare Worker/D1 (production) + FastAPI backend (local dev), deployed to Cloudflare Pages.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static build → out/

# Backend (local dev — serves timemachine.db)
cd pipeline && .venv/bin/python -m generate_asset_snapshot.server  # FastAPI (port 8000)

# Worker (production — serves D1)
cd worker && npx wrangler dev --remote              # local proxy to D1

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 24 test files
cd pipeline && .venv/bin/mypy generate_asset_snapshot/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# Build timemachine DB
cd pipeline && python3 scripts/build_timemachine_db.py
cd pipeline && python3 scripts/sync_to_d1.py        # push to D1

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

Next.js static shell on Cloudflare Pages. Production data served by Cloudflare Worker (`worker/src/index.ts`) reading from D1. For local dev, FastAPI backend (`pipeline/generate_asset_snapshot/server.py`) serves data from SQLite (`pipeline/data/timemachine.db`) on port 8000.

Frontend fetches all data in a single `GET /timeline` call, then computes allocation, cashflow, activity, and reconciliation locally in `use-bundle.ts`. Brush drag is zero-latency (no network). Other endpoints (`/allocation`, `/activity`, `/cashflow`, `/market`, `/holdings-detail`) still exist in FastAPI but are unused by the frontend.

R2 (`latest.json`) is legacy and being phased out. The `/econ` page still reads from R2 (`econ.json`).
