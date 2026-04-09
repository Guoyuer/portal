# D1 + Workers Migration Design

Replace the local FastAPI + SQLite backend with Cloudflare D1 + Workers. The Worker is a thin JSON proxy; all heavy computation stays in the Python pipeline (precompute) and the browser (runtime).

## Architecture

```
Pipeline (Python, local)
  build_timemachine_db.py → SQLite (existing)
  sync_to_d1.py (new)    → wrangler d1 execute → D1

Worker (Cloudflare, TypeScript)
  GET /timeline → 7 SELECTs from D1 views → JSON → CDN cache

Frontend (unchanged)
  useBundle() → fetch(TIMELINE_URL) → Worker
```

## What Changes

### Pipeline — precompute market + holdings detail

**New tables written by pipeline:**

`computed_market` — one row per index ticker:
```sql
CREATE TABLE computed_market (
  ticker      TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  current     REAL NOT NULL,
  month_return REAL NOT NULL,
  ytd_return  REAL NOT NULL,
  high_52w    REAL NOT NULL,
  low_52w     REAL NOT NULL,
  sparkline   TEXT NOT NULL,  -- JSON array of ~252 floats
  -- scalar macro indicators stored as rows with special ticker keys
  -- e.g. ticker='__usdCny', current=7.28, others=0
);
```

Scalar macro indicators (usdCny, fedRate, treasury10y, cpi, unemployment, vix) stored as rows in the same table with special ticker keys (e.g. `__usdCny`). This avoids a second table while keeping everything structured.

`computed_holdings_detail` — per-ticker performance:
```sql
CREATE TABLE computed_holdings_detail (
  ticker       TEXT PRIMARY KEY,
  month_return REAL NOT NULL,
  start_value  REAL NOT NULL,
  end_value    REAL NOT NULL,
  high_52w     REAL,
  low_52w      REAL,
  vs_high      REAL
);
```

**New script:** `pipeline/scripts/sync_to_d1.py`
- Dumps relevant tables from local SQLite as SQL INSERT statements
- Runs `wrangler d1 execute portal-db --file=dump.sql --remote`
- Clears existing data before import (full replace)

### D1 — schema + camelCase views

Create D1 database `portal-db`. Schema reuses existing SQLite tables plus the two new precomputed tables above.

**Views for camelCase output (Worker reads views, not raw tables):**

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

CREATE VIEW v_daily_tickers AS
SELECT date, ticker, value, category, subtype,
  cost_basis AS costBasis, gain_loss AS gainLoss,
  gain_loss_pct AS gainLossPct
FROM computed_daily_tickers ORDER BY date, value DESC;

CREATE VIEW v_fidelity_txns AS
SELECT run_date AS runDate, action, symbol, amount
FROM fidelity_transactions ORDER BY id;

CREATE VIEW v_qianji_txns AS
SELECT date, type, category, amount
FROM qianji_transactions ORDER BY date;

CREATE VIEW v_market AS
SELECT ticker, name, current,
  month_return AS monthReturn, ytd_return AS ytdReturn,
  high_52w AS high52w, low_52w AS low52w, sparkline
FROM computed_market ORDER BY ticker;

CREATE VIEW v_holdings_detail AS
SELECT ticker, month_return AS monthReturn,
  start_value AS startValue, end_value AS endValue,
  high_52w AS high52w, low_52w AS low52w, vs_high AS vsHigh
FROM computed_holdings_detail ORDER BY month_return DESC;
```

### Worker — single endpoint

**Location:** `worker/src/index.ts` + `worker/wrangler.toml`

```ts
export default {
  async fetch(request, env) {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/timeline") {
      return new Response("Not found", { status: 404 });
    }

    const [daily, prefix, tickers, fidelity, qianji, market, holdings] =
      await Promise.all([
        env.DB.prepare("SELECT * FROM v_daily").all(),
        env.DB.prepare("SELECT * FROM v_prefix").all(),
        env.DB.prepare("SELECT * FROM v_daily_tickers").all(),
        env.DB.prepare("SELECT * FROM v_fidelity_txns").all(),
        env.DB.prepare("SELECT * FROM v_qianji_txns").all(),
        env.DB.prepare("SELECT * FROM v_market").all(),
        env.DB.prepare("SELECT * FROM v_holdings_detail").all(),
      ]);

    // Split market rows: indices vs scalar indicators
    const indices = [];
    const meta = {};
    for (const r of market.results) {
      if (r.ticker.startsWith("__")) {
        meta[r.ticker.slice(2)] = r.current;
      } else {
        indices.push({ ...r, sparkline: JSON.parse(r.sparkline) });
      }
    }

    // Top/bottom performers
    const allHoldings = holdings.results;
    const holdingsDetail = {
      topPerformers: allHoldings.slice(0, 5),
      bottomPerformers: allHoldings.slice(-5).reverse(),
      upcomingEarnings: [],
    };

    return Response.json({
      daily: daily.results,
      prefix: prefix.results,
      dailyTickers: tickers.results,
      fidelityTxns: fidelity.results,
      qianjiTxns: qianji.results,
      market: { indices, ...meta },
      holdingsDetail,
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
| Rows read | 5M/day | ~33k (800+800+28k+1.8k+2k+10+30) | ~150/day |
| Rows written | 100k/day | 0 (writes via wrangler) | N/A |
| Storage | 5GB | ~6MB | N/A |

With Cache-Control (1hr), most page loads hit CDN. Personal use = ~10-20 uncached/day = well within free tier.

## What Does NOT Change

- Frontend code (useBundle, components, computation logic)
- Pipeline computation (allocation, prefix sums, ingestion)
- Econ dashboard (stays on R2)
- Local dev workflow (FastAPI server still works for dev)

## Migration Steps

1. Pipeline: precompute market returns + holdings detail into new tables
2. Pipeline: sync_to_d1.py script (SQLite → D1 via wrangler)
3. D1: create database, schema, views
4. Worker: implement + deploy
5. Frontend: update TIMELINE_URL for production
6. CI: add Worker deploy step
7. Verify: compare Worker response vs FastAPI response
