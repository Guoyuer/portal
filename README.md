# Portal

Personal one-stop dashboard — finance reports, email triage, news, and economic analysis. Currently serves the financial report from [assetSnapshot](https://github.com/Guoyuer/assetSnapshot); more modules planned.

**Live:** https://portal-bf8.pages.dev

## Architecture

```mermaid
graph TB
    subgraph "Your Machine"
        DEV[npm run dev<br/>localhost:3000]
        PUSH[git push]
    end

    subgraph "GitHub"
        REPO[(Guoyuer/portal)]
        CI[CI Workflow<br/>build + e2e tests]
        DEPLOY[Deploy Workflow<br/>build + wrangler deploy]
    end

    subgraph "Cloudflare"
        PAGES[Cloudflare Pages<br/>portal-bf8.pages.dev]
        ACCESS[Cloudflare Access<br/>auth · planned]
    end

    PUSH --> REPO
    REPO -->|push to main| CI
    REPO -->|push to main| DEPLOY
    DEPLOY -->|static HTML| PAGES
    ACCESS -.->|protects| PAGES

    style PAGES fill:#f59e0b,color:#000
    style CI fill:#27ae60,color:#fff
    style DEPLOY fill:#2563eb,color:#fff
```

## How Deployment Works

Every push to `main` triggers two parallel GitHub Actions workflows:

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub Actions
    participant CF as Cloudflare Pages

    Dev->>GH: git push main
    par CI Pipeline
        GH->>GH: npm ci
        GH->>GH: next build
        GH->>GH: playwright install
        GH->>GH: playwright test (10 e2e tests)
        GH-->>Dev: pass or fail
    and Deploy Pipeline
        GH->>GH: npm ci
        GH->>GH: next build (output: export → /out)
        GH->>CF: wrangler pages deploy out/
        CF-->>Dev: Live at portal-bf8.pages.dev
    end
```

Key detail: `next.config.ts` sets `output: "export"`, which makes Next.js produce **pure static HTML/CSS/JS** in the `out/` directory. No server needed — Cloudflare Pages serves the files directly from its edge CDN.

## Project Structure

```mermaid
graph LR
    subgraph "src/app · Routes"
        PAGE_HOME["/ → redirect to /finance"]
        PAGE_FIN["/finance · report page"]
    end

    subgraph "src/lib · Data Layer"
        TYPES[types.ts<br/>ReportData interfaces]
        SAMPLE[sample-data.ts<br/>snapshot from real report]
        FORMAT[format.ts<br/>$, %, ¥ formatters]
    end

    subgraph "src/components · UI"
        SIDEBAR[layout/sidebar.tsx<br/>navigation · client component]
        SHADCN[ui/*<br/>Card Table Badge Button]
    end

    PAGE_FIN --> TYPES
    PAGE_FIN --> SAMPLE
    PAGE_FIN --> FORMAT
    PAGE_FIN --> SHADCN
    PAGE_HOME --> PAGE_FIN

    style PAGE_FIN fill:#2563eb,color:#fff
    style SIDEBAR fill:#1e293b,color:#fff
```

```
portal/
├── src/
│   ├── app/
│   │   ├── layout.tsx              # Root layout + sidebar
│   │   ├── page.tsx                # / → redirects to /finance
│   │   ├── globals.css             # Tailwind + shadcn theme
│   │   └── finance/
│   │       └── page.tsx            # Finance report (Server Component)
│   ├── components/
│   │   ├── layout/
│   │   │   └── sidebar.tsx         # Nav sidebar (Client Component)
│   │   └── ui/                     # shadcn/ui primitives
│   │       ├── card.tsx
│   │       ├── table.tsx
│   │       ├── badge.tsx
│   │       ├── button.tsx
│   │       └── separator.tsx
│   └── lib/
│       ├── types.ts                # TypeScript interfaces (mirrors assetSnapshot's ReportData)
│       ├── sample-data.ts          # Real report data snapshot for dev/testing
│       └── format.ts               # fmtCurrency, fmtPct, fmtYuan
├── e2e/
│   └── finance.spec.ts             # 10 Playwright e2e tests
├── .github/workflows/
│   ├── ci.yml                      # Build + e2e on push/PR
│   └── deploy.yml                  # Build + deploy to Cloudflare Pages
├── next.config.ts                  # Static export mode
├── playwright.config.ts
└── package.json
```

## Report Sections

The finance page renders data matching the [assetSnapshot](https://github.com/Guoyuer/assetSnapshot) HTML report:

```mermaid
graph TD
    A["Metric Cards<br/>Portfolio · Net Worth · Savings Rate · Goal"] --> B["Category Summary<br/>equity & non-equity allocation vs targets"]
    B --> C["Cash Flow<br/>income · expenses · savings rates"]
    C --> D["Investment Activity<br/>deposits · buys · sells · dividends"]
    D --> E["Balance Sheet<br/>assets · liabilities · net worth"]

    style A fill:#f8f9fa,stroke:#333
    style B fill:#f8f9fa,stroke:#333
    style C fill:#f8f9fa,stroke:#333
    style D fill:#f8f9fa,stroke:#333
    style E fill:#f8f9fa,stroke:#333
```

Collapsible rows (native `<details>`) for:
- **Expenses** below $200 threshold → "... and N more" expandable
- **Activity tickers** beyond top 5 → expandable overflow

## Data Flow (Current vs Planned)

```mermaid
graph LR
    subgraph "Now"
        SD["sample-data.ts<br/>hardcoded snapshot"]
        FP1[Finance Page]
        SD --> FP1
    end

    subgraph "Planned"
        AS["assetSnapshot<br/>Python CLI"]
        GCS[("Cloud Storage")]
        R2[("Cloudflare R2")]
        CRON["GitHub Actions<br/>weekly cron"]
        FP2[Finance Page]
        D1[("Cloudflare D1")]

        AS -->|generate JSON| GCS
        CRON -->|download + transform| R2
        R2 --> FP2
        D1 -->|"news · mail · econ"| FP2
    end

    style SD fill:#f59e0b,color:#000
    style R2 fill:#2563eb,color:#fff
    style D1 fill:#2563eb,color:#fff
```

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Framework | Next.js 15 (App Router) | Marketable, React ecosystem, file-based routing |
| Styling | Tailwind CSS v4 + shadcn/ui | Utility-first, copy-paste components (not npm dep) |
| Fonts | Geist Sans + Geist Mono | Clean, designed for dashboards |
| Hosting | Cloudflare Pages | Edge CDN, free tier, no cold starts |
| Auth (planned) | Cloudflare Access | Zero-trust, Google login, no code |
| Database (planned) | Cloudflare D1 (SQLite) | No pausing, portable, generous free tier |
| Storage (planned) | Cloudflare R2 (S3-compatible) | Reports, data blobs |
| CI | GitHub Actions | Build + Playwright e2e tests |
| E2E Tests | Playwright | Chromium, 10 tests, runs in CI |

## Adding a New Module

Each module follows the same pattern:

```
src/app/{module}/page.tsx        ← route + UI
src/lib/{module}-types.ts        ← data interfaces
src/lib/{module}-data.ts         ← data fetching
e2e/{module}.spec.ts             ← tests
```

Planned modules: **Mail** (Gmail API + AI triage), **News** (RSS aggregation), **Economy** (FRED/Yahoo indicators).

## Development

```bash
npm install
npm run dev          # http://localhost:3000

# Run e2e tests (builds first)
npx next build
npx playwright test

# Preview production build locally
npx serve out -l 3000
```

## Secrets

| Secret | Where | Purpose |
|--------|-------|---------|
| `CLOUDFLARE_ACCOUNT_ID` | GitHub repo secrets | Cloudflare account identifier |
| `CLOUDFLARE_API_TOKEN` | GitHub repo secrets | Wrangler deploy permission |
