# Portal

Personal one-stop dashboard. Finance reports with live data from Fidelity + Qianji, with email triage, news, and economic analysis planned.

**Live:** https://portal-bf8.pages.dev

## Architecture

```mermaid
graph TB
    subgraph "Mac (launchd, daily)"
        SYNC["sync.py<br/>detect new CSVs + Qianji DB"]
    end

    subgraph "Cloudflare R2"
        RAW["latest/<br/>positions.csv · history.csv<br/>qianjiapp.db · config.json"]
        JSON["reports/<br/>latest.json"]
    end

    subgraph "GitHub Actions"
        CI["CI<br/>Python lint/test + Node build + e2e"]
        REPORT["Report (weekly cron)<br/>generate JSON + HTML email"]
        DEPLOY["Deploy (on push)<br/>build static shell + deploy"]
    end

    subgraph "Cloudflare Pages"
        PAGES["portal-bf8.pages.dev<br/>static shell + client-side fetch"]
    end

    SYNC -->|"wrangler r2 put<br/>(only if MD5 changed)"| RAW
    RAW -->|download| REPORT
    REPORT -->|JSON| JSON
    REPORT -->|HTML| EMAIL[Gmail]
    DEPLOY -->|static shell| PAGES
    JSON -->|"fetch on page load"| PAGES

    style SYNC fill:#10b981,color:#fff
    style REPORT fill:#2563eb,color:#fff
    style PAGES fill:#f59e0b,color:#000
```

**Key design:** Portal is a static shell (HTML + JS) deployed to Cloudflare Pages. On every page load, the browser fetches the latest report data directly from R2. No rebuild needed when data changes — only when code changes.

## Data Pipeline

```mermaid
sequenceDiagram
    participant Mac as Mac (launchd)
    participant R2 as Cloudflare R2
    participant GA as GitHub Actions
    participant GM as Gmail
    participant User as Browser

    Note over Mac: Daily at 9AM + on login
    Mac->>R2: sync.py (CSVs + Qianji DB, MD5 dedup)

    Note over GA: Weekly Monday 9AM ET
    GA->>R2: wrangler r2 get (raw data)
    GA->>GA: Check freshness (sync_meta.json)
    alt Data > 7 days old
        GA->>GM: Reminder email
    else Fresh data
        GA->>GA: Python: build ReportData
        GA->>GA: Render HTML (email) + JSON (portal)
        GA->>GM: Send HTML report email
        GA->>R2: Upload latest.json
    end

    Note over User: Any time
    User->>R2: fetch latest.json (on page load or Reload button)
    R2->>User: Report data (JSON)
```

## Project Structure

```
portal/
├── src/                               # Next.js frontend (TypeScript)
│   ├── app/
│   │   ├── layout.tsx                 # Root layout + sidebar
│   │   ├── page.tsx                   # / → redirects to /finance
│   │   └── finance/
│   │       └── page.tsx               # Finance report (client component, fetches R2)
│   ├── components/
│   │   ├── layout/sidebar.tsx         # Nav sidebar (Client Component)
│   │   └── ui/                        # shadcn/ui (Card, Table, Badge, Button, etc.)
│   └── lib/
│       ├── types.ts                   # 1:1 camelCase mirror of Python ReportData
│       ├── config.ts                  # R2 public URL
│       └── format.ts                  # Currency/percent/yuan formatters
│
├── pipeline/                          # Report generation (Python)
│   ├── generate_asset_snapshot/       # Core package
│   │   ├── report.py                  # build_report() → ReportData
│   │   ├── types.py                   # Source-of-truth dataclasses
│   │   ├── renderers/
│   │   │   ├── html.py                # Email-safe HTML renderer
│   │   │   └── json_renderer.py       # dataclasses.asdict() + camelCase (~20 lines)
│   │   ├── ingest/                    # Fidelity CSV + Qianji DB parsers
│   │   ├── market/                    # Yahoo Finance + FRED APIs
│   │   └── ...
│   ├── scripts/
│   │   ├── sync.py                    # Mac → R2 (wrangler CLI, MD5 dedup)
│   │   ├── send_report.py             # Generate HTML + JSON, send email
│   │   └── install_launchd.sh         # macOS scheduled sync setup
│   ├── tests/                         # 201 Python tests
│   ├── config.json                    # Asset classification config
│   └── requirements.txt               # yfinance, fredapi
│
├── e2e/
│   └── finance.spec.ts                # 19 Playwright e2e tests
│
├── .github/workflows/
│   ├── ci.yml                         # Python (pytest/mypy/ruff) + Node (build + e2e)
│   ├── deploy.yml                     # Build static shell → deploy to Cloudflare Pages
│   └── report.yml                     # Weekly: generate report → email → upload JSON to R2
│
└── package.json
```

## Type Contract

Zero translation layer between Python and TypeScript:

```mermaid
graph LR
    PY["Python types.py<br/>(source of truth)"] -->|"dataclasses.asdict()<br/>+ camelCase keys"| JSON["latest.json<br/>(R2)"]
    JSON -->|"fetch + render"| TS["TypeScript types.ts<br/>(1:1 mirror)"]

    style PY fill:#3776ab,color:#fff
    style JSON fill:#f59e0b,color:#000
    style TS fill:#3178c6,color:#fff
```

- Python `snake_case` → JSON `camelCase` → TypeScript `camelCase`
- JSON renderer is ~20 lines (`dataclasses.asdict()` + recursive key conversion)
- Raw transaction lists stripped from JSON (portal uses pre-computed aggregations)
- No manual field mapping, no divergent schemas

## Report Sections

```mermaid
graph TD
    A["Metric Cards<br/>Portfolio · Net Worth · Savings Rate · Goal"] --> B["Category Summary<br/>equity & non-equity allocation vs targets"]
    B --> C["Cash Flow<br/>income · expenses · savings rates"]
    C --> D["Investment Activity<br/>net cash in · deployed · passive income"]
    D --> E["Balance Sheet<br/>Fidelity + personal accounts + CNY + credit"]

    style A fill:#f8f9fa,stroke:#333
    style B fill:#f8f9fa,stroke:#333
    style C fill:#f8f9fa,stroke:#333
    style D fill:#f8f9fa,stroke:#333
    style E fill:#f8f9fa,stroke:#333
```

Charts (Recharts):
- **Allocation donut** — 5-category pie with center total, next to Category Summary table
- **Income vs Expenses** — grouped bars + savings rate line overlay (24 months)
- **Net Worth Trend** — area chart (renders when historical data available)

Collapsible rows (native `<details>`) for expenses < $200 and activity tickers beyond top 5.

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Frontend | Next.js 15 (App Router) | Marketable, React ecosystem, file-based routing |
| Charts | Recharts | Lightweight, React-native, ComposedChart for mixed bar+line |
| Data fetch | Client-side fetch from R2 | No rebuild needed for data updates, Reload button |
| Styling | Tailwind CSS v4 + shadcn/ui | Utility-first, dark mode support |
| Fonts | Geist Sans + Geist Mono | Clean, designed for dashboards |
| Hosting | Cloudflare Pages | Edge CDN, free tier, static shell |
| Storage | Cloudflare R2 (public) | S3-compatible, free 10GB, CORS enabled |
| Auth | Cloudflare Access | Zero-trust, Google login on portal.guoyuer.com |
| Domain | guoyuer.com | Custom domain via Cloudflare Registrar |
| Pipeline | Python 3.14 | Fidelity/Qianji parsing, Yahoo/FRED APIs |
| CI | GitHub Actions | Python quality gates + Node build + Playwright e2e |
| E2E Tests | Playwright (28 tests) | Charts, dark mode, reload, market, prod URL |
| Database (planned) | Cloudflare D1 (SQLite) | For future modules (mail, news, econ) |

## Development

```bash
# Install
npm install
cd pipeline && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Dev server (fetches live data from R2)
npm run dev              # http://localhost:3000

# Run tests
npx next build && npx playwright test        # 28 e2e tests
cd pipeline && .venv/bin/pytest -q            # 200 Python tests

# Manual sync to R2
cd pipeline && python3 scripts/sync.py --force

# Generate report manually
cd pipeline && python3 scripts/send_report.py --data-dir ./data --dry-run
```

## Setup (one-time)

1. **Cloudflare R2**: Create bucket `asset-snapshot-data`, enable public access (r2.dev URL), set CORS to `AllowedOrigins: ["*"]`
2. **Custom domain**: Register domain on Cloudflare, add `portal.yourdomain.com` to Pages project
3. **Cloudflare Access**: Zero Trust → Add Google IdP → Create Access Application for portal domain
4. **GitHub Secrets**: `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` (Pages + R2 Edit), `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`
5. **Mac sync**: `wrangler login && bash pipeline/scripts/install_launchd.sh`
6. **First sync**: `cd pipeline && python3 scripts/sync.py --force`

## Adding a New Module

```
src/app/{module}/page.tsx        ← route + UI
src/lib/{module}-config.ts       ← R2 URLs / data loading
e2e/{module}.spec.ts             ← tests
pipeline/...                     ← data generation (if needed)
```

Planned: **Mail** (Gmail API + AI triage), **News** (RSS aggregation), **Economy** (FRED/Yahoo dashboard).

## TODO

- [ ] Gmail module — important email auto-triage
- [ ] News aggregation — RSS feeds
- [ ] Economic indicators dashboard — FRED time series charts
- [ ] Net Worth Trend chart — needs historical snapshot data
- [ ] Mobile responsiveness check
- [ ] Last updated timestamp on page
