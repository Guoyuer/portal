# D1 + Workers Migration Design

Replace the local FastAPI + SQLite backend with Cloudflare D1 + Workers. The Worker is a thin JSON proxy; all heavy computation stays in the Python pipeline (precompute) and the browser (runtime).

## Architecture

```
Pipeline (Python, local/CI)
  build_timemachine_db.py → SQLite (existing)
  sync_to_d1.py (new)    → wrangler d1 execute → D1

Worker (Cloudflare, TypeScript)
  GET /timeline → 7 SELECTs from D1 views → JSON → CDN cache

Frontend (unchanged)
  useBundle() → fetch(TIMELINE_URL) → Worker
```

## What Changes

### Pipeline — precompute market + holdings + JSON blobs

**New tables written by pipeline:**

`computed_market` — one row per index ticker, precomputed by pipeline:
```sql
CREATE TABLE computed_market (
  ticker     TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  current    REAL NOT NULL,
  monthReturn REAL NOT NULL,
  ytdReturn  REAL NOT NULL,
  high52w    REAL NOT NULL,
  low52w     REAL NOT NULL,
  sparkline  TEXT NOT NULL   -- JSON array of ~252 floats
);
```

`computed_market_meta` — scalar macro indicators:
```sql
CREATE TABLE computed_market_meta (
  key   TEXT PRIMARY KEY,
  value REAL
);
-- rows: usdCny, fedRate, treasury10y, cpi, unemployment, vix
```

`computed_holdings_detail` — top/bottom performers:
```sql
CREATE TABLE computed_holdings_detail (
  ticker      TEXT PRIMARY KEY,
  monthReturn REAL NOT NULL,
  startValue  REAL NOT NULL,
  endValue    REAL NOT NULL,
  high52w     REAL,
  low52w      REAL,
  vsHigh      REAL
);
```

`computed_daily_json` — pre-serialized ticker arrays (28k rows → 800 rows):
```sql
CREATE TABLE computed_daily_json (
  date         TEXT PRIMARY KEY,
  tickers_json TEXT NOT NULL  -- JSON array of ticker objects
);
```

**New script:** `pipeline/scripts/sync_to_d1.py`
- Dumps relevant tables from local SQLite
- Runs `wrangler d1 execute portal-db --file=dump.sql --remote`
- Clears existing data before import (full replace, not incremental)

### D1 — schema + camelCase views

Create D1 database `portal-db`. Schema is the existing SQLite tables plus the new precomputed tables.

**Views for camelCase output (Worker reads these, not raw tables):**

```sql
CREATE VIEW v_daily AS
SELECT date, total,
  us_equity AS usEquity, non_us_equity AS nonUsEquity,
  crypto, safe_net AS safeNet, liabilities
FROM computed_daily ORDER BY date;

CREATE VIEW v_prefix AS
SELECT date, income, expenses, buys, sells, dividends,
  net_cash_in AS netCashIn, cc_payments AS ccPayments
FROM computed_prefix ORDER BY date;

CREATE VIEW v_daily_json AS
SELECT date, tickers_json AS tickersJson
FROM computed_daily_json ORDER BY date;

CREATE VIEW v_fidelity_txns AS
SELECT run_date AS runDate, action, symbol, amount
FROM fidelity_transactions ORDER BY id;

CREATE VIEW v_qianji_txns AS
SELECT date, type, category, amount
FROM qianji_transactions ORDER BY date;
```

Market and holdings detail views are trivial (columns already camelCase from pipeline).

### Worker — single endpoint, ~20 lines

**Location:** `worker/src/index.ts` + `worker/wrangler.toml`

```ts
export default {
  async fetch(request, env) {
    // CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/timeline") {
      return new Response("Not found", { status: 404 });
    }

    const [daily, prefix, tickerJson, fidelity, qianji, market, marketMeta, holdings] =
      await Promise.all([
        env.DB.prepare("SELECT * FROM v_daily").all(),
        env.DB.prepare("SELECT * FROM v_prefix").all(),
        env.DB.prepare("SELECT * FROM v_daily_json").all(),
        env.DB.prepare("SELECT * FROM v_fidelity_txns").all(),
        env.DB.prepare("SELECT * FROM v_qianji_txns").all(),
        env.DB.prepare("SELECT * FROM computed_market").all(),
        env.DB.prepare("SELECT * FROM computed_market_meta").all(),
        env.DB.prepare("SELECT * FROM computed_holdings_detail").all(),
      ]);

    // Assemble market object
    const meta = Object.fromEntries(marketMeta.results.map(r => [r.key, r.value]));
    const marketObj = {
      indices: market.results.map(r => ({ ...r, sparkline: JSON.parse(r.sparkline) })),
      ...meta,
    };

    // Assemble holdings detail
    const sorted = [...holdings.results].sort((a, b) => b.monthReturn - a.monthReturn);
    const holdingsObj = {
      topPerformers: sorted.slice(0, 5),
      bottomPerformers: sorted.slice(-5).reverse(),
      upcomingEarnings: [],
    };

    return Response.json({
      daily: daily.results,
      prefix: prefix.results,
      dailyTickers: tickerJson.results.flatMap(r => JSON.parse(r.tickersJson)),
      fidelityTxns: fidelity.results,
      qianjiTxns: qianji.results,
      market: marketObj,
      holdingsDetail: holdingsObj,
    }, {
      headers: {
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
      },
    });
  },
};
```

### Frontend — config change only

- `.env.local`: `NEXT_PUBLIC_TIMELINE_URL=https://portal-api.<account>.workers.dev/timeline`
- CI build: pass Worker URL as env var
- Zero code changes in `use-bundle.ts` or components

### CI/CD

Add to `.github/workflows/ci.yml`:
```yaml
- name: Deploy Worker
  if: github.ref == 'refs/heads/main' && github.event_name == 'push'
  run: cd worker && npx wrangler deploy
  env:
    CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
    CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

## D1 Free Tier Budget

| Resource | Limit | Per /timeline request | Requests to exhaust |
|----------|-------|----------------------|---------------------|
| Rows read | 5M/day | ~5k (with JSON blobs) | ~1000/day |
| Rows written | 100k/day | 0 (writes via wrangler) | N/A |
| Storage | 5GB | 6MB used | N/A |

Cache-Control (1 hour) means most page loads hit CDN, not D1. Personal use = well within free tier.

## What Does NOT Change

- Frontend code (useBundle, components, computation logic)
- Pipeline computation (allocation, prefix sums, ingestion)
- Econ dashboard (stays on R2)
- Local dev workflow (FastAPI server still works for dev)

## Migration Steps (high level)

1. Pipeline: add precompute for market, holdings, daily_json
2. Pipeline: add sync_to_d1.py script
3. D1: create database + schema + views
4. Worker: implement and deploy
5. Frontend: update TIMELINE_URL for production
6. CI: add Worker deployment step
7. Verify: compare Worker response vs FastAPI response byte-for-byte
