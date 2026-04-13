# Portal

Personal one-stop dashboard. Finance reports with live data from Fidelity brokerage + [Qianji](https://qianjiapp.com/) expense tracking + Empower 401k, plus an economic indicators dashboard (FRED). More modules planned.

**Live:** https://portal.guoyuer.com (protected by Cloudflare Access)

## Architecture

```mermaid
graph TB
    subgraph "Local build"
        BUILD["build_timemachine_db.py<br/>ingest → replay → precompute"]
        DB[(timemachine.db)]
    end

    subgraph "Cloudflare D1 + Workers"
        D1[(D1 portal-db)]
        WORKER["Worker<br/>GET /timeline · /econ · /prices/:sym"]
    end

    subgraph "GitHub Actions"
        CI["CI + Deploy<br/>Python lint/test + vitest + Playwright<br/>+ Pages + Worker deploy"]
    end

    subgraph "Cloudflare"
        ACCESS["Cloudflare Access<br/>Google login"]
        PAGES["Cloudflare Pages<br/>static shell + PWA"]
    end

    subgraph "Browser"
        SW["Service Worker<br/>offline cache"]
        RC["React Compiler<br/>auto-memoization"]
        COMPUTE["use-bundle.ts<br/>client-side compute"]
    end

    BUILD --> DB
    DB -->|sync_to_d1.py| D1
    D1 --> WORKER
    CI -->|static shell| PAGES
    WORKER -->|"fetch /timeline"| SW
    SW --> COMPUTE
    ACCESS -->|protects| PAGES

    style BUILD fill:#10b981,color:#fff
    style WORKER fill:#2563eb,color:#fff
    style PAGES fill:#f59e0b,color:#000
    style ACCESS fill:#f97316,color:#fff
    style D1 fill:#2563eb,color:#fff
```

**Key design:** Portal is a static shell (HTML + JS) deployed to Cloudflare Pages. The Cloudflare Worker serves `GET /timeline` from D1 (SQLite-compatible). The frontend fetches once on load, then computes allocation, cashflow, activity, and reconciliation locally in `use-bundle.ts`. Brush drag is zero-latency (no network round-trips).

## Gmail Auto-Triage (`/mail` tab)

Daily cron reads unread Gmail, classifies via Claude Haiku, and caches results in a separate D1. The `/mail` page shows three sections (IMPORTANT / NEUTRAL / TRASH_CANDIDATE); delete button on a row does an IMAP trash via the Worker.

```mermaid
graph TB
    subgraph "GitHub Actions (daily 07:00)"
        CRON["gmail-sync.yml<br/>cron: 0 22 * * * UTC"]
        PY["triage.py<br/>IMAP fetch → Claude classify<br/>(batch 30 · strip ```json fences ·<br/>normalize msg_id brackets)"]
    end

    subgraph "Gmail"
        IMAP["imap.gmail.com<br/>UNSEEN SINCE yesterday"]
    end

    subgraph "Anthropic"
        HAIKU["Claude Haiku 4.5<br/>system + few-shot prompt"]
    end

    subgraph "worker-gmail (Cloudflare)"
        WSYNC["POST /mail/sync<br/>(SYNC_SECRET)"]
        WLIST["GET /mail/list<br/>(USER_KEY)"]
        WTRASH["POST /mail/trash<br/>(USER_KEY)"]
        D1M[(D1 portal-gmail<br/>triaged_emails)]
        SOCK["cloudflare:sockets<br/>hand-rolled IMAP<br/>UID STORE +X-GM-LABELS \\Trash"]
    end

    subgraph "Browser (/mail)"
        MAIL["Next.js page<br/>useMail hook<br/>localStorage key"]
    end

    CRON --> PY
    PY -->|IMAP SEARCH + FETCH| IMAP
    IMAP --> PY
    PY -->|messages.create| HAIKU
    HAIKU --> PY
    PY -->|POST per batch| WSYNC
    WSYNC -->|INSERT OR IGNORE| D1M

    MAIL -->|X-Mail-Key| WLIST
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

**Design decisions** (see `docs/gmail-triage-design-2026-04-12.md`):
- One Gmail app password covers everything (SMTP send not needed since digest was dropped; IMAP read in Python + IMAP trash in Worker via `cloudflare:sockets` TCP).
- `INSERT OR IGNORE` preserves user-set `status='trashed'` across daily re-syncs.
- URL key (`USER_KEY`) stored in browser localStorage; strict constant-time compare on the Worker. `SYNC_SECRET` gates the GH Actions → Worker sync channel.
- No `etl.email_report` / SMTP reuse — v1 surfaces triage in the UI, not as digest email.
- Known follow-up: both Workers expose `.workers.dev` URLs with no CF Access. See `docs/security-worker-backdoor-2026-04-12.md`.

## Data Pipeline

```mermaid
sequenceDiagram
    participant Local as Local build
    participant D1 as Cloudflare D1
    participant Worker as Worker
    participant User as Browser

    Local->>Local: build_timemachine_db.py<br/>(ingest → replay → precompute)
    Local->>D1: sync_to_d1.py (wrangler CLI)

    Note over User: Any time
    User->>Worker: fetch /timeline
    Worker->>D1: SELECTs (views)
    D1->>Worker: rows
    Worker->>User: JSON (no-cache)

    Note over User: On ticker click
    User->>Worker: fetch /prices/:symbol
    Worker->>D1: daily_close + fidelity_transactions
    D1->>Worker: rows
    Worker->>User: JSON
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
│   │       └── page.tsx               # Gmail triage tab (client, URL-key auth)
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
│   │   │   ├── market-context.tsx     # Index returns + macro indicators
│   │   │   └── net-worth-growth.tsx   # MoM/YoY growth rates
│   │   ├── econ/
│   │   │   ├── macro-cards.tsx        # Economic snapshot cards
│   │   │   └── time-series-chart.tsx  # Multi-line FRED chart viewer
│   │   ├── mail/
│   │   │   ├── mail-list.tsx          # 3-section grouped list
│   │   │   ├── mail-row.tsx           # single email row with actions
│   │   │   └── delete-button.tsx      # optimistic IMAP trash button
│   │   └── ui/                        # shadcn/ui (Button, Table)
│   └── lib/
│       ├── use-bundle.ts              # Core data hook: fetch /timeline → local compute
│       ├── use-mail.ts                # Gmail triage hook (key-resolve + optimistic delete)
│       ├── schemas/                   # Zod API schemas (timeline, econ, ticker, mail) + index
│       ├── computed-types.ts          # Client-computed TS types (not Zod-derived)
│       ├── compute.ts                 # Pure computation (allocation, cashflow, activity)
│       ├── config.ts                  # WORKER_BASE, TIMELINE_URL, ECON_URL, GOAL
│       ├── format.ts                  # Currency/percent formatters
│       ├── hooks.ts                   # Shared React hooks (inc. getIsDark / useIsDark)
│       ├── chart-styles.ts            # Recharts theming
│       ├── thresholds.ts              # Business thresholds + value coloring
│       └── utils.ts                   # General utilities
│
├── worker/                            # Cloudflare Worker (TypeScript) — Finance/Econ
│   ├── src/index.ts                   # GET /timeline, /econ, /prices/:symbol → D1 → JSON
│   ├── schema.sql                     # D1 tables + camelCase views
│   ├── wrangler.toml                  # D1 binding config
│   ├── tsconfig.json
│   └── package.json
│
├── worker-gmail/                      # Cloudflare Worker (TypeScript) — Gmail triage
│   ├── src/index.ts                   # POST /mail/sync, GET /mail/list, POST /mail/trash
│   │                                  # Includes hand-rolled IMAP via cloudflare:sockets
│   ├── src/db.ts                      # D1 helpers (INSERT OR IGNORE, list, markTrashed)
│   ├── src/types.ts                   # Category / UpsertInput / TriagedEmail
│   ├── schema.sql                     # triaged_emails table + indexes
│   ├── wrangler.jsonc                 # D1 binding + nodejs_compat
│   ├── tsconfig.json
│   └── package.json
│
├── pipeline/                          # Data pipeline (Python)
│   ├── etl/       # Core package
│   │   ├── db.py                      # SQLite schema + connection helpers
│   │   ├── timemachine.py             # Historical replay engine
│   │   ├── allocation.py              # Compute daily per-asset allocation
│   │   ├── precompute.py              # Build computed_* tables (daily, market)
│   │   ├── incremental.py             # Incremental DB update mode
│   │   ├── validate.py                # Post-build validation gate
│   │   ├── prices.py                  # Yahoo Finance price + CNY rate fetcher
│   │   ├── empower_401k.py            # Empower 401k QFX snapshot parser
│   │   ├── types.py                   # Source-of-truth dataclasses
│   │   ├── config.py                  # JSON config loader
│   │   ├── ingest/
│   │   │   ├── fidelity_history.py    # Fidelity transaction CSV parser
│   │   │   ├── robinhood_history.py   # Robinhood transaction CSV parser
│   │   │   └── qianji_db.py           # Qianji SQLite reader
│   │   ├── market/
│   │   │   ├── yahoo.py               # Yahoo Finance: index returns, CNY rate
│   │   │   └── fred.py                # FRED API: Fed rate, CPI, VIX, oil, etc.
│   │   └── reconcile.py               # Qianji ↔ Fidelity cross-reconciliation
│   ├── scripts/
│   │   ├── build_timemachine_db.py    # Main build: ingest → replay → precompute → SQLite
│   │   ├── sync_to_d1.py             # Push timemachine.db tables to D1
│   │   ├── gen_schema_sql.py          # Auto-generate worker/schema.sql from db.py
│   │   ├── verify_positions.py        # Verify Fidelity replay accuracy
│   │   ├── inspect_qianji.py          # Debug/inspect Qianji replay (no drift gate)
│   │   ├── create_test_db.py          # Generate test fixture DB
│   │   └── gmail/                     # Gmail triage daily classifier (GH Actions)
│   │       ├── triage.py              # CLI: fetch 24h unread → classify → POST /mail/sync
│   │       ├── imap_client.py         # imaplib + MIME parse
│   │       ├── classify.py            # Anthropic Haiku, batched + fence-strip + bracket-match
│   │       └── worker_sync.py         # httpx POST to worker-gmail
│   ├── tests/                         # Unit + contract tests
│   │   ├── unit/                      # Unit tests
│   │   ├── contract/                  # Data invariant tests
│   │   └── fixtures/                  # Sample CSVs, QFX files
│   ├── data/
│   │   └── timemachine.db             # Generated SQLite (not in repo)
│   ├── pyproject.toml                 # pytest, mypy, ruff config
│   ├── requirements.txt               # yfinance, fredapi, httpx
│   └── config.example.json            # Template config
│
├── e2e/                               # Playwright e2e tests
│   ├── finance.spec.ts                # Finance dashboard tests
│   ├── econ.spec.ts                   # Economy dashboard tests
│   ├── perf-brush.spec.ts             # Brush performance tests
│   └── interactive-check.spec.ts      # Interactive component tests
│
├── .github/workflows/
│   ├── ci.yml                         # Python + Node CI → Pages + Worker deploy
│   └── gmail-sync.yml                 # Daily 22:00 UTC → run gmail/triage.py --sync
│
└── package.json
```

## Type Contract

Zero translation layer between Python and TypeScript:

```mermaid
graph LR
    PY["Python types.py<br/>(source of truth)"] -->|"precompute → SQLite"| DB["timemachine.db"]
    DB -->|"sync_to_d1.py"| D1["D1"]
    D1 -->|"Worker views<br/>(camelCase aliases)"| JSON["GET /timeline<br/>(JSON)"]
    JSON -->|"Zod validation"| TS["TypeScript schema.ts<br/>(camelCase mirror)"]

    style PY fill:#3776ab,color:#fff
    style DB fill:#10b981,color:#fff
    style D1 fill:#2563eb,color:#fff
    style JSON fill:#f59e0b,color:#000
    style TS fill:#3178c6,color:#fff
```

- Python `snake_case` → D1 views `camelCase` aliases → TypeScript `camelCase`
- Frontend validates with Zod schemas (`schema.ts`)
- Raw transaction lists are included for local computation in `use-bundle.ts`
- No manual field mapping, no divergent schemas

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | Next.js 16 (App Router) + React Compiler | Auto-memoization, View Transitions |
| Charts | Recharts 3 | SVG (accessible for colorblind), brush interaction |
| Validation | Zod 4 | Runtime schema validation at API boundary |
| Data | `use-bundle.ts` → Worker `/timeline` | Fetch once, compute locally, zero-lag brush |
| Styling | Tailwind CSS v4 + Container Queries | `@container`-based responsive cards |
| Offline | Service Worker (PWA) | Cache-first static, stale-while-revalidate API |
| Hosting | Cloudflare Pages + Workers | Edge CDN, D1 SQLite, free tier |
| Storage | Cloudflare D1 | Structured data via Worker |
| Auth | Cloudflare Access | Zero-trust, Google login |
| Pipeline | Python 3.14 | Fidelity/Qianji/Robinhood/401k ingest, Yahoo Finance, FRED API |
| CI/CD | GitHub Actions | Python lint/test + vitest + Playwright E2E + deploy |
| Tests | vitest (115) + Playwright (5 specs, mock API) + pytest (466) | Coverage thresholds, branch protection |
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
NEXT_PUBLIC_TIMELINE_URL=http://localhost:8787/timeline
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
8. **Gmail triage (optional)**: `cd worker-gmail && npx wrangler d1 create portal-gmail` → apply `schema.sql` → `wrangler secret put` for `SYNC_SECRET`, `USER_KEY`, `SMTP_USER`, `SMTP_PASSWORD` → `wrangler deploy`. Add GH secrets `PORTAL_SMTP_*`, `PORTAL_GMAIL_WORKER_URL`, `PORTAL_GMAIL_SYNC_SECRET`, `ANTHROPIC_API_KEY`. First visit: `/mail?key=<USER_KEY>` once.

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
