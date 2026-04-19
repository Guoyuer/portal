# Portal

Personal one-stop dashboard. Finance reports with live data from Fidelity brokerage + [Qianji](https://qianjiapp.com/) expense tracking + Empower 401k, plus an economic indicators dashboard (FRED). More modules planned.

**Live:** https://portal.guoyuer.com (protected by Cloudflare Access)

## Architecture

```mermaid
graph TB
    subgraph Local["Local machine"]
        TASK["Windows Task Scheduler<br/>run_portal_sync.ps1 (AtLogOn + 2m)"]
        AUTO["run_automation.py<br/>detect → build → verify → sync"]
        BUILD["build_timemachine_db.py<br/>ingest → replay → precompute"]
        DB[(timemachine.db)]
        SYNC["sync_to_d1.py<br/>diff (default) or --full"]
    end

    subgraph Cloud["Cloudflare — portal.guoyuer.com (single-origin behind CF Access)"]
        ACCESS["CF Access<br/>Google SSO cookie"]
        PAGES["/* Pages<br/>static shell + Service Worker"]
        WAPI["/api/* portal-api Worker<br/>GET /timeline · /econ · /prices/:sym<br/>edge cache 60s / 600s / 300s"]
        WMAIL["/api/mail/* worker-gmail Worker<br/>GET list · POST trash"]
        D1[(D1 portal-db)]
        D1M[(D1 portal-gmail)]
    end

    subgraph Browser
        SW["Service Worker<br/>cache-first static · SWR API"]
        UI["React 19 + React Compiler<br/>(auto-memoization)"]
        COMPUTE["src/lib/compute/compute.ts<br/>allocation · cashflow · activity"]
    end

    subgraph CI["GitHub Actions"]
        CI_TEST["pytest + vitest + Playwright (mock API)"]
        CI_DEPLOY["Pages deploy<br/>(Workers deploy is manual — token<br/>lacks Zone → Workers Routes → Edit)"]
    end

    TASK --> AUTO --> BUILD --> DB --> SYNC --> D1
    ACCESS -.gates.-> PAGES & WAPI & WMAIL
    PAGES -->|initial load| SW --> UI --> COMPUTE
    UI -->|"fetch /api/timeline · /econ · /prices/:sym"| WAPI --> D1
    UI -->|"fetch /api/mail/{list,trash}"| WMAIL --> D1M
    CI_TEST --> CI_DEPLOY --> PAGES

    style BUILD fill:#10b981,color:#fff
    style WAPI fill:#2563eb,color:#fff
    style WMAIL fill:#2563eb,color:#fff
    style PAGES fill:#f59e0b,color:#000
    style ACCESS fill:#f97316,color:#fff
    style D1 fill:#2563eb,color:#fff
    style D1M fill:#2563eb,color:#fff
```

**Key design:** Portal is a static shell deployed to Cloudflare Pages. Two Workers are mounted as zone routes on the same origin (`portal.guoyuer.com/api/*` → `portal-api`; `portal.guoyuer.com/api/mail/*` → `worker-gmail`) so every `/api/*` call shares the same CF Access session cookie — no CORS, no cross-subdomain handshake. The frontend fetches once on load via `GET /api/timeline`, then computes allocation, cashflow, activity, and reconciliation locally in `src/lib/compute/compute.ts` via `src/lib/hooks/use-bundle.ts`. Brush drag is zero-latency (no network round-trips). Ticker dialogs fetch `GET /api/prices/:symbol` on demand.

## Gmail Auto-Triage (`/mail` tab)

Daily cron reads unread Gmail, classifies via Claude Haiku, and caches results in a separate D1. The `/mail` page shows three sections (IMPORTANT / NEUTRAL / TRASH_CANDIDATE); delete button on a row does an IMAP trash via the Worker.

```mermaid
graph TB
    subgraph "GitHub Actions (daily 07:00 +08 = 22:00 UTC)"
        CRON["gmail-sync.yml<br/>cron: 0 22 * * *"]
        PY["triage.py<br/>IMAP fetch → Claude classify<br/>(batch 30 · strip ```json fences ·<br/>normalize msg_id brackets)"]
    end

    subgraph "Gmail"
        IMAP["imap.gmail.com<br/>UNSEEN SINCE yesterday"]
    end

    subgraph "Anthropic"
        HAIKU["Claude Haiku 4.5<br/>system + few-shot prompt"]
    end

    subgraph "worker-gmail (Cloudflare)"
        WSYNC["POST /mail/sync<br/>(SYNC_SECRET)<br/>portal-mail.guoyuer.com"]
        WLIST["GET /api/mail/list<br/>(CF Access)"]
        WTRASH["POST /api/mail/trash<br/>(CF Access)"]
        D1M[(D1 portal-gmail<br/>triaged_emails)]
        SOCK["cloudflare:sockets<br/>hand-rolled IMAP<br/>UID STORE +X-GM-LABELS \\Trash"]
    end

    subgraph "Browser (/mail)"
        MAIL["Next.js page<br/>useMail hook<br/>same-origin fetch"]
    end

    CRON --> PY
    PY -->|IMAP SEARCH + FETCH| IMAP
    IMAP --> PY
    PY -->|messages.create| HAIKU
    HAIKU --> PY
    PY -->|POST per batch| WSYNC
    WSYNC -->|INSERT OR IGNORE| D1M

    MAIL -->|same-origin fetch| WLIST
    WLIST -->|SELECT last 7d active| D1M
    D1M --> WLIST
    WLIST --> MAIL

    MAIL -->|click Delete| WTRASH
    WTRASH --> SOCK
    SOCK -->|LOGIN + UID SEARCH +<br/>UID STORE +X-GM-LABELS| IMAP
    WTRASH -->|UPDATE status=trashed| D1M

    style PY fill:#10b981,color:#fff
    style HAIKU fill:#7c3aed,color:#fff
    style WSYNC fill:#2563eb,color:#fff
    style WLIST fill:#2563eb,color:#fff
    style WTRASH fill:#2563eb,color:#fff
    style D1M fill:#2563eb,color:#fff
    style IMAP fill:#dc2626,color:#fff
```

**Design decisions** (original spec: `docs/gmail-triage-design-2026-04-12.md`; browser auth superseded by PRs #137-#139 — see `docs/archive/security-worker-backdoor-2026-04-12.md`):
- One Gmail app password covers everything (SMTP send not needed since digest was dropped; IMAP read in Python + IMAP trash in Worker via `cloudflare:sockets` TCP).
- `INSERT OR IGNORE` preserves user-set `status='trashed'` across daily re-syncs.
- Browser auth is Cloudflare Access on `portal.guoyuer.com` (same-origin `/api/mail/*`); no in-app URL key. `SYNC_SECRET` gates the GH Actions → Worker sync channel on `portal-mail.guoyuer.com/mail/sync`.
- No `etl.email_report` / SMTP reuse — v1 surfaces triage in the UI, not as digest email.

## Data Pipeline

```mermaid
sequenceDiagram
    participant Local as Local build
    participant D1 as Cloudflare D1
    participant Worker as Worker
    participant User as Browser

    Local->>Local: build_timemachine_db.py<br/>(ingest → replay → precompute)
    Local->>D1: sync_to_d1.py (diff by default)

    Note over User: Page load
    User->>Worker: GET /api/timeline
    Worker->>D1: parallel SELECTs (views)
    D1->>Worker: rows
    Worker->>User: JSON ~385 KB gzip (edge cache 60s)

    Note over User: Econ page
    User->>Worker: GET /api/econ
    Worker->>D1: econ_series + v_econ_snapshot
    D1->>Worker: rows
    Worker->>User: JSON (edge cache 600s)

    Note over User: On ticker click
    User->>Worker: GET /api/prices/:symbol
    Worker->>D1: daily_close + transactions
    D1->>Worker: rows
    Worker->>User: JSON (edge cache 300s)
```

## Project Structure

```
portal/
├── src/                               # Next.js frontend (TypeScript)
│   ├── app/
│   │   ├── layout.tsx                 # Root layout + sidebar
│   │   ├── page.tsx                   # / → redirects to /finance
│   │   ├── finance/
│   │   │   └── page.tsx               # Finance dashboard (client component)
│   │   ├── econ/
│   │   │   └── page.tsx               # Economy dashboard (FRED charts)
│   │   └── mail/
│   │       └── page.tsx               # Gmail triage tab (client, CF Access cookie auth)
│   ├── components/
│   │   ├── layout/
│   │   │   ├── sidebar.tsx            # Nav sidebar
│   │   │   ├── theme-toggle.tsx       # Dark mode toggle
│   │   │   └── back-to-top.tsx        # Floating scroll-to-top
│   │   ├── finance/
│   │   │   ├── section.tsx            # SectionHeader + SectionBody layout primitives
│   │   │   ├── ticker-table.tsx       # TickerTable + DeviationCell
│   │   │   ├── charts.tsx             # Recharts (donut, bar+line, area)
│   │   │   ├── timemachine.tsx        # Brush/traveller date-range selector
│   │   │   ├── metric-cards.tsx       # Portfolio, Net Worth, Savings Rate, Goal
│   │   │   ├── category-summary.tsx   # Allocation table + donut
│   │   │   ├── cash-flow.tsx          # Income/expenses + summary
│   │   │   ├── ticker-chart.tsx       # Per-ticker price chart with buy/sell markers
│   │   │   ├── ticker-chart-base.tsx  # Shared price-chart primitive (AreaChart + markers)
│   │   │   ├── ticker-markers.tsx     # Buy/sell/dividend markers on ticker charts
│   │   │   ├── ticker-dialog.tsx      # Modal: per-ticker price chart + transaction table
│   │   │   └── market-context.tsx     # Index returns + macro indicators
│   │   ├── charts/
│   │   │   └── tooltip-card.tsx       # Shared Recharts tooltip card primitive
│   │   ├── econ/
│   │   │   ├── macro-cards.tsx        # Economic snapshot cards
│   │   │   └── time-series-chart.tsx  # Multi-line FRED chart viewer
│   │   ├── mail/
│   │   │   ├── mail-list.tsx          # 3-section grouped list
│   │   │   ├── mail-row.tsx           # single email row with actions
│   │   │   └── delete-button.tsx      # optimistic IMAP trash button
│   │   ├── error-boundary.tsx         # Section-level ErrorBoundary + fallback card
│   │   ├── loading-skeleton.tsx       # Suspense fallbacks (finance + econ)
│   │   └── ui/                        # shadcn/ui (Button, Table)
│   └── lib/
│       ├── config.ts                  # WORKER_BASE, TIMELINE_URL, ECON_URL, GOAL
│       ├── utils.ts                   # General utilities (cn, etc.)
│       ├── compute/
│       │   ├── compute.ts             # Pure computation (allocation, cashflow, activity)
│       │   └── computed-types.ts      # Client-computed TS types (not Zod-derived)
│       ├── format/
│       │   ├── format.ts              # Currency/percent/date formatters
│       │   ├── econ-formatters.ts     # Macro-indicator value formatters
│       │   ├── chart-styles.ts        # Recharts theming
│       │   ├── chart-colors.ts        # Okabe-Ito palette + category color map
│       │   ├── thresholds.ts          # Business thresholds + value coloring
│       │   └── ticker-data.ts         # Price/transaction merge helper for ticker charts
│       ├── hooks/
│       │   ├── use-bundle.ts          # Core data hook: fetch /timeline → local compute
│       │   ├── use-mail.ts            # Gmail triage hook (optimistic delete)
│       │   └── hooks.ts               # Shared React hooks (useIsDark, useIsMobile, ...)
│       └── schemas/                   # Zod API schemas (timeline, econ, ticker, mail)
│           └── _generated.ts          # Auto-generated from pipeline/etl/types.py
│
├── worker/                            # Cloudflare Worker (TypeScript) — Finance/Econ
│   ├── src/
│   │   ├── index.ts                   # GET /timeline, /econ, /prices/:symbol → D1 → JSON
│   │   ├── config.ts                  # Endpoint cache TTLs, error-shape helpers
│   │   └── utils.ts                   # cachedJson, settled(), D1 row helpers
│   ├── schema.sql                     # D1 tables + camelCase views (auto-generated)
│   ├── wrangler.toml                  # D1 binding config
│   ├── dev-remote.sh                  # `wrangler dev --remote` through CF Access
│   ├── tsconfig.json
│   └── package.json
│
├── worker-gmail/                      # Cloudflare Worker (TypeScript) — Gmail triage
│   ├── src/
│   │   ├── index.ts                   # POST /mail/sync, GET /mail/list, POST /mail/trash
│   │   ├── imap-parse.ts              # Hand-rolled IMAP framing over cloudflare:sockets
│   │   ├── db.ts                      # D1 helpers (INSERT OR IGNORE, list, markTrashed)
│   │   ├── types.ts                   # Category / UpsertInput / TriagedEmail
│   │   └── utils.ts                   # Response helpers, auth gates
│   ├── schema.sql                     # triaged_emails table + indexes
│   ├── wrangler.jsonc                 # D1 binding + nodejs_compat
│   ├── tsconfig.json
│   └── package.json
│
├── pipeline/                          # Data pipeline (Python)
│   ├── etl/                           # Core package
│   │   ├── db.py                      # SQLite schema + connection helpers
│   │   ├── allocation.py              # Compute daily per-asset allocation
│   │   ├── replay.py                  # Source-agnostic cost-basis replay primitive
│   │   ├── precompute.py              # Build computed_* tables (daily, market, holdings)
│   │   ├── refresh.py                 # Incremental refresh window start
│   │   ├── validate.py                # Post-build validation gate
│   │   ├── categories.py              # Category metadata loader
│   │   ├── qianji.py                  # Qianji SQLite ingest + reverse replay
│   │   ├── types.py                   # Source-of-truth TypedDicts + dataclasses
│   │   ├── projection.py              # Net-worth projection (nightly)
│   │   ├── email_report.py            # SMTP digest sender
│   │   ├── dotenv_loader.py           # .env loader used by entry scripts
│   │   ├── sources/                   # Investment sources (InvestmentSource Protocol)
│   │   │   ├── __init__.py            # SOURCES list + Protocol + PositionRow + ActionKind
│   │   │   ├── fidelity/              # CSV ingest + classify + cash + pricing
│   │   │   ├── robinhood.py           # CSV ingest + ReplayConfig
│   │   │   └── empower.py             # QFX ingest + contribution fallback
│   │   ├── prices/                    # Price + CNY-rate fetching (Yahoo Finance)
│   │   ├── market/                    # Market-data fetchers
│   │   │   ├── yahoo.py               # Index returns, CNY, DXY, USD/CNY
│   │   │   └── fred.py                # FRED API: Fed rate, CPI, VIX, oil, ...
│   │   ├── changelog/                 # Nightly changelog diff + email formatter
│   │   ├── automation/                # run_automation helpers (change detection, exit codes)
│   │   └── migrations/                # Idempotent in-place schema resyncs
│   │       └── add_fidelity_action_kind.py  # Continuous classifier resync
│   ├── scripts/
│   │   ├── build_timemachine_db.py    # Main build: ingest → replay → precompute → SQLite
│   │   ├── sync_to_d1.py              # Push timemachine.db tables to D1 (diff or --full)
│   │   ├── run_automation.py          # Orchestrates detect → build → verify → sync
│   │   ├── run_portal_sync.ps1        # Task Scheduler shim (forwards args)
│   │   ├── gen_schema_sql.py          # Auto-generate worker/schema.sql from db.py
│   │   ├── verify_positions.py        # Verify replayed shares vs Portfolio_Positions CSVs
│   │   ├── verify_vs_prod.py          # Local vs prod D1 row-count parity gate
│   │   ├── backup_d1.py               # Pull remote D1 → local SQLite snapshot
│   │   ├── sync_prices_nightly.py     # Nightly price refresh (cron)
│   │   ├── project_networth_nightly.py # Nightly net-worth projection (cron)
│   │   ├── refresh_l1_baseline_from_fixtures.py  # Regenerate L1 hashes after behavior change
│   │   ├── seed_local_d1_from_fixtures.sh  # Populate local D1 for offline dev
│   │   └── gmail/                     # Gmail triage daily classifier (GH Actions)
│   │       ├── triage.py              # CLI: fetch 24h unread → classify → POST /mail/sync
│   │       ├── imap_client.py         # imaplib + MIME parse
│   │       ├── classify.py            # Anthropic Haiku, batched + fence-strip + bracket-match
│   │       └── worker_sync.py         # httpx POST to worker-gmail
│   ├── tests/                         # Unit + contract + regression (L1 + L2)
│   │   ├── unit/                      # Unit tests
│   │   ├── contract/                  # Data invariant tests
│   │   ├── regression/                # L1 row-hash + L2 fixture golden
│   │   └── fixtures/                  # Sample CSVs, QFX files
│   ├── tools/
│   │   └── gen_zod.py                 # Regenerate src/lib/schemas/_generated.ts
│   ├── data/
│   │   └── timemachine.db             # Generated SQLite (not in repo)
│   ├── pyproject.toml                 # pytest, mypy, ruff config
│   ├── requirements.txt               # yfinance, fredapi, httpx, anthropic
│   └── config.example.json            # Template config
│
├── e2e/                               # Playwright e2e tests
│   ├── mock-api.ts                    # Mock /timeline + /econ + /prices (port 4444)
│   ├── finance.spec.ts                # Finance dashboard tests
│   ├── econ.spec.ts                   # Economy dashboard tests
│   ├── ticker-dialog.spec.ts          # Per-ticker modal interaction
│   ├── fail-open.spec.ts              # Partial-failure fallbacks render error cards
│   ├── perf-brush.spec.ts             # Brush performance tests
│   ├── real-worker.spec.ts            # Optional: run against a live Worker
│   └── manual/                        # Ad-hoc exploratory specs
│
├── .github/workflows/
│   ├── ci.yml                         # Python + Node CI → Pages deploy
│   ├── gmail-sync.yml                 # Daily 22:00 UTC → run gmail/triage.py --sync
│   ├── prices-sync.yml                # Nightly price refresh
│   ├── d1-backup.yml                  # Periodic D1 → SQLite snapshot
│   ├── e2e-real-worker.yml            # Optional Playwright run against live Worker
│   └── regression-baseline-refresh.yml # `baseline-refresh` PR label → refresh L1 hashes
│
└── package.json
```

## Type Contract

Zero translation layer between Python and TypeScript:

```mermaid
graph LR
    PY["pipeline/etl/types.py<br/>(TypedDicts + dataclasses)"] -->|"gen_zod.py<br/>(parity-checked in pytest)"| GEN["src/lib/schemas/_generated.ts"]
    PY -->|"precompute → SQLite"| DB["timemachine.db"]
    DB -->|"sync_to_d1.py"| D1["D1 portal-db<br/>+ camelCase views"]
    D1 -->|"Worker SELECT → JSON"| JSON["GET /api/timeline"]
    GEN -->|"extend() / omit()"| TS["src/lib/schemas/timeline.ts"]
    JSON -->|"safeParse at boundary"| TS

    style PY fill:#3776ab,color:#fff
    style DB fill:#10b981,color:#fff
    style D1 fill:#2563eb,color:#fff
    style JSON fill:#f59e0b,color:#000
    style TS fill:#3178c6,color:#fff
```

- Python `snake_case` → D1 views `camelCase` aliases → TypeScript `camelCase`
- Schemas auto-generated from `etl/types.py` via `tools/gen_zod.py` (pytest parity check)
- Frontend validates at the boundary with Zod (`src/lib/schemas/`); Worker ships raw D1 rows (no runtime Zod — the frontend parse is the single drift checkpoint)
- Raw transaction lists are shipped in `/timeline` for local computation in `src/lib/hooks/use-bundle.ts`
- No manual field mapping, no divergent schemas

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | Next.js 16 (App Router) + React Compiler | Auto-memoization, View Transitions |
| Charts | Recharts 3 | SVG (accessible for colorblind), brush interaction |
| Validation | Zod 4 | Runtime schema validation at API boundary |
| Data | `use-bundle.ts` → Worker `GET /api/timeline` | Fetch once, compute locally, zero-lag brush |
| Styling | Tailwind CSS v4 + Container Queries | `@container`-based responsive cards |
| Offline | Service Worker (PWA) | Cache-first static, stale-while-revalidate API |
| Hosting | Cloudflare Pages + Workers | Edge CDN, D1 SQLite, free tier |
| Storage | Cloudflare D1 | Structured data via Worker |
| Auth | Cloudflare Access | Zero-trust, Google login |
| Pipeline | Python 3.14 | Fidelity/Qianji/Robinhood/401k ingest, Yahoo Finance, FRED API |
| CI/CD | GitHub Actions | Python lint/test + vitest + Playwright E2E + deploy |
| Tests | vitest (24 files) + Playwright (6 specs, mock API) + pytest (45 files) | Coverage thresholds, branch protection |
| Errors | Sentry | Client-side error tracking in production |

## Development

```bash
# Install
npm install
cd pipeline && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Config (copy template and fill in your accounts)
cp pipeline/config.example.json pipeline/config.json

# Pipeline env vars (SMTP, FRED API key) — optional, auto-loaded by entry scripts.
# setx-persisted User vars take precedence; .env is a dev convenience.
cp pipeline/.env.example pipeline/.env  # then edit

# Environment (create .env.local)
cat > .env.local <<EOF
NEXT_PUBLIC_TIMELINE_URL=http://localhost:8787
EOF

# Worker (local proxy to remote D1)
cd worker && npx wrangler dev --remote   # http://localhost:8787

# Dev server (fetches from TIMELINE_URL)
npm run dev              # http://localhost:3000

# Gmail triage — dry-run against real Gmail, skip Worker sync
# (requires PORTAL_SMTP_USER/PASSWORD + ANTHROPIC_API_KEY in env)
cd pipeline && .venv/Scripts/python.exe scripts/gmail/triage.py --sync --dry-run

# Run tests
cd pipeline && .venv/bin/pytest -q                          # Python tests
cd pipeline && .venv/bin/mypy etl/ --ignore-missing-imports
cd pipeline && .venv/bin/ruff check .
npx playwright test                                          # e2e tests (mock API, no backend needed)

# Build timemachine DB from raw data
cd pipeline && python3 scripts/build_timemachine_db.py

# Sync DB to Cloudflare D1 (diff, default)
cd pipeline && python3 scripts/sync_to_d1.py

# Automated pipeline: detect changes → build → verify → sync
# (orchestration lives in run_automation.py; PS1 is a thin Task Scheduler shim)
cd pipeline && .venv/Scripts/python.exe scripts/run_automation.py
```

## Setup (one-time)

1. **Cloudflare D1**: `cd worker && npx wrangler d1 create portal-db`, apply schema: `npx wrangler d1 execute portal-db --remote --file=schema.sql`
2. **Environment**: Set `NEXT_PUBLIC_TIMELINE_URL` (Worker URL) in `.env.local` and as GitHub secret
3. **Custom domain** (optional): Add `portal.yourdomain.com` to Pages project
4. **Cloudflare Access** (optional): Zero Trust → Add Google IdP → Access Application
5. **GitHub Secrets**: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN`, `NEXT_PUBLIC_TIMELINE_URL`, `FRED_API_KEY`
6. **Config**: Copy `config.example.json` → `config.json`, fill in your accounts
7. **First build**: `cd pipeline && python3 scripts/build_timemachine_db.py && python3 scripts/sync_to_d1.py`
8. **Gmail triage (optional)**: `cd worker-gmail && npx wrangler d1 create portal-gmail` → apply `schema.sql` → `wrangler secret put` for `SYNC_SECRET`, `SMTP_USER`, `SMTP_PASSWORD` → `wrangler deploy`. Add GH secrets `PORTAL_SMTP_*`, `PORTAL_GMAIL_CRON_URL`, `PORTAL_GMAIL_SYNC_SECRET`, `ANTHROPIC_API_KEY`. Browser auth relies on the same Cloudflare Access app that gates `portal.guoyuer.com`.

## Adding a New Module

```
src/app/{module}/page.tsx        ← route + UI
src/lib/schemas/{module}.ts      ← Zod schemas (re-exported from schemas/index.ts)
src/components/{module}/         ← components
e2e/{module}.spec.ts             ← tests
pipeline/...                     ← data generation (if needed)
```

## Roadmap

- [x] Gmail module — important email auto-triage (daily classification + one-click trash, see `docs/gmail-triage-design-2026-04-12.md`)
- [ ] News aggregation — RSS feeds
- [ ] AI-generated macro narrative — LLM summarizing economic conditions and cycle position

## License

[MIT](LICENSE)
