# Portal

## This is NOT the Next.js you know

This version has breaking changes -- APIs, conventions, and file structure may differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` before writing Next.js code. Heed deprecation notices.

Personal finance dashboard: Next.js 16 static frontend + Cloudflare Worker + R2 JSON artifacts, deployed to Cloudflare Pages. The same Worker binary serves local dev and production.

## Commands

```bash
# Frontend
npm run dev                                         # dev server (port 3000)
npm run build                                       # static export -> out/
npm run test                                        # vitest run
npx playwright test                                 # mock API e2e

# Backend Worker
cd worker && npx wrangler dev --local               # local R2, seed first
cd worker && npx wrangler deploy                    # deploy portal-api
bash worker/dev-remote.sh                           # --remote through CF Access when needed

# Python pipeline
cd pipeline && .venv/Scripts/python.exe -m pytest -q
cd pipeline && .venv/Scripts/python.exe -m mypy etl/ --strict --ignore-missing-imports
cd pipeline && .venv/Scripts/python.exe -m ruff check .

# Build timemachine DB
cd pipeline && .venv/Scripts/python.exe scripts/build_timemachine_db.py

# R2 artifact publish path
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py export
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py verify
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py publish --remote
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py publish --local

# Regenerate Zod schemas from etl/types.py
cd pipeline && .venv/Scripts/python.exe tools/gen_zod.py --write ../src/lib/schemas/_generated.ts

# Regression gate
cd pipeline && .venv/Scripts/python.exe -m pytest tests/regression/ -v
cd pipeline && .venv/Scripts/python.exe scripts/refresh_l1_baseline_from_fixtures.py

# Automated pipeline
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --dry-run
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py --force --local

# Local R2 fixture seed
bash pipeline/scripts/seed_local_r2_from_fixtures.sh
```

**Git Bash / MSYS gotcha:** `NEXT_PUBLIC_*=/path npx next build` in Git Bash translates the value into `C:/Program Files/Git/path`. Prefix such commands with `MSYS_NO_PATHCONV=1` or run from CMD/PowerShell.

## Code Style

- Python: ruff (line-length 120), mypy strict, `from __future__ import annotations`.
- TypeScript: strict, path alias `@/*` -> `src/`.
- Section dividers: `# --` (Python) / `// --` (TypeScript) are acceptable; preserve nearby style.
- React Compiler is enabled project-wide. Do not add manual `useMemo` / `useCallback`; move expensive transforms upstream into `src/lib/compute/compute.ts` or `src/lib/format/`.

## Type Contract

Python `etl/types.py` is the source for generated Zod schemas:

`etl/types.py` -> `pipeline/tools/gen_zod.py` -> `src/lib/schemas/_generated.ts`.

Local SQLite `timemachine.db` is the source of truth for data. `pipeline/scripts/r2_artifacts.py` exports endpoint-shaped JSON from SQLite API projections, verifies hashes/row counts/latest date/Zod schemas, publishes versioned objects to R2, and flips `manifest.json` last. The Worker streams those artifacts; it does not run SQL.

Pipeline-internal types that never cross the artifact/Worker/Zod boundary live in `pipeline/etl/sources/_types.py`:

- `ActionKind`
- `PriceContext`
- `PositionRow`
- `InvestmentSource`

## Architecture

Next.js static shell on Cloudflare Pages (`output: "export"`). Data is served by a single Cloudflare Worker (`worker/src/index.ts`, deployed as `portal-api`) reading endpoint artifacts from R2 bucket `portal-data`. The Worker is mounted as a same-origin zone route on `portal.guoyuer.com/api/*`.

The pipeline builds `pipeline/data/timemachine.db`, exports JSON artifacts under `pipeline/artifacts/r2`, verifies them, and publishes to R2 manifest-last. SQLite remains available locally for ad-hoc SQL debugging.

### Investment Sources

Investment sources live under `pipeline/etl/sources/`:

- `fidelity/` -- directory module for CSV parsing, pricing, and cash reconstruction.
- `robinhood.py`, `empower.py` -- single-file sources.
- `_types.py` -- Protocol + shared dataclasses.
- `_ingest.py` -- shared range-replace idempotency helper.

Each source exposes `ingest(db_path, config)`, `positions_at(db_path, ctx) -> list[PositionRow]`, and `produces_positions(config) -> bool`. `compute_daily_allocation` and `positions_at_all` iterate `SOURCES`; allocation should not contain broker-specific code.

Qianji stays outside the `InvestmentSource` Protocol because it models categorical cash flows, not investment positions. Yahoo/FRED market data also stays outside because it produces time series.

### Frontend Data Flow

Frontend fetches `GET /timeline` once, computes allocation/cashflow/activity/reconciliation locally, and fetches `GET /prices` lazily for ticker/group charts. `GET /econ` backs the economy page.

Key pure compute outputs:

- `computeCategories`, `computeSnapshot`
- `computeMonthlyFlows`
- `computeActivity`, `computeGroupedActivity`
- `computeCrossCheck`

`use-bundle.ts` is a thin orchestrator over `use-timeline-data.ts`, `use-brush-range.ts`, and `compute-bundle.ts`. `use-timeline-data.ts` Zod `safeParse` is the single runtime drift checkpoint.

### Worker

Serves 3 endpoints:

- `GET /timeline`
- `GET /econ`
- `GET /prices`

Routes strip optional `/api` so the same code serves `workers.dev`, local `wrangler dev`, and the production zone route. The Worker owns manifest lookup, R2 object streaming with `no-store` headers, and explicit 5xx failures for missing or invalid artifacts.

### R2 Artifacts

`r2_artifacts.py export` writes:

- `manifest.json`
- `snapshots/<version>/timeline.json`
- `snapshots/<version>/econ.json`
- `snapshots/<version>/prices.json`
- `reports/export-summary.json`

`publish` verifies locally first, enforces a single-publisher lock, refuses to overwrite existing remote snapshot objects, uploads snapshot objects, readback-checks hashes, then publishes `manifest.json` last.

### Load-Bearing Boundaries

Do not simplify these without redesigning the data-publication correctness model:

- `manifest.json` hash and byte descriptors
- remote upload readback verification
- single-publisher lock
- frontend Zod runtime parse
- publish-time Zod artifact validation
- local SQLite `timemachine.db`
- Worker fail-closed behavior for missing or invalid artifacts
- per-symbol transactions inside `prices.json`

## Accessibility

Chart colors use the Okabe-Ito colorblind-friendly palette. Categorical encodings are paired with text/letters/shapes; color alone is insufficient.
