@AGENTS.md

# Portal

Personal finance dashboard: Next.js frontend + FastAPI backend (SQLite), deployed to Cloudflare Pages.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static build → out/

# Backend
cd pipeline && .venv/Scripts/python -m generate_asset_snapshot.server  # FastAPI (port 8000)

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 140+ unit tests
cd pipeline && .venv/bin/mypy generate_asset_snapshot/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# E2E (local only — skipped in CI)
npx next build && npx playwright test               # 28 Playwright tests
```

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)

## Type contract

Python `types.py` (snake_case) is source of truth → JSON (camelCase) → TypeScript `types.ts` (camelCase mirror). Keep them in sync.

## Architecture

Next.js static shell on Cloudflare Pages. FastAPI backend (`pipeline/generate_asset_snapshot/server.py`) serves data from SQLite (`pipeline/data/timemachine.db`) on port 8000.

Frontend fetches all data in a single `GET /timeline` call, then computes allocation, cashflow, activity, and reconciliation locally in `use-bundle.ts`. Brush drag is zero-latency (no network). Other endpoints (`/allocation`, `/activity`, `/cashflow`, `/market`, `/holdings-detail`) still exist but are unused by the frontend.

R2 (`latest.json`) is legacy and being phased out.
