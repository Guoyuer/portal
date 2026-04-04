@AGENTS.md

# Portal

Personal finance dashboard: Next.js 15 frontend + Python pipeline, deployed to Cloudflare Pages with data on R2.

## Commands

```bash
# Frontend
npm run dev                                         # dev server
npm run build                                       # static build → output/

# Python pipeline
cd pipeline && .venv/bin/pytest -q                  # 140+ unit tests
cd pipeline && .venv/bin/mypy generate_asset_snapshot/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .

# E2E
npx next build && npx playwright test               # 28 Playwright tests
```

## Code style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`
- TypeScript: strict, path alias `@/*` → `src/`
- Section dividers: `# ──` (Python) / `// ──` (TypeScript)

## Type contract

Python `types.py` (snake_case) is source of truth → JSON (camelCase) → TypeScript `types.ts` (camelCase mirror). Keep them in sync.

## Architecture

Static shell on Cloudflare Pages. Browser fetches `latest.json` from R2 on page load. No server-side data fetching — pipeline generates JSON offline (GitHub Actions weekly).
