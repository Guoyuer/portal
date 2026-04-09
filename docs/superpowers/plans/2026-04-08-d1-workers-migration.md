# D1 + Workers Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace local FastAPI + SQLite backend with Cloudflare D1 + Workers so the finance dashboard works without a local server.

**Architecture:** Pipeline precomputes market + holdings detail into two new DB tables, then syncs the entire SQLite DB to D1 via wrangler CLI. A Cloudflare Worker serves `GET /timeline` by running 7 SELECTs against D1 views (camelCase aliases) and returning JSON with 1hr CDN cache. Frontend changes only the URL config.

**Tech Stack:** Cloudflare Workers (TypeScript), D1 (SQLite-compatible), Wrangler CLI, existing Python pipeline.

---

### Task 1: Pipeline — precompute market data

**Files:**
- Modify: `pipeline/generate_asset_snapshot/precompute.py`
- Modify: `pipeline/generate_asset_snapshot/db.py` (add table + init)
- Modify: `pipeline/scripts/build_timemachine_db.py` (call precompute)
- Test: `pipeline/tests/unit/test_precompute_market.py`

- [ ] **Step 1: Add computed_market table to schema**

In `pipeline/generate_asset_snapshot/db.py`, add to `_TABLES`:

```sql
CREATE TABLE IF NOT EXISTS computed_market (
    ticker       TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    current      REAL NOT NULL DEFAULT 0,
    month_return REAL NOT NULL DEFAULT 0,
    ytd_return   REAL NOT NULL DEFAULT 0,
    high_52w     REAL NOT NULL DEFAULT 0,
    low_52w      REAL NOT NULL DEFAULT 0,
    sparkline    TEXT NOT NULL DEFAULT '[]'
);
```

- [ ] **Step 2: Write failing test for precompute_market**

Create `pipeline/tests/unit/test_precompute_market.py`:

```python
"""Tests for market + holdings detail precomputation."""
from __future__ import annotations

from pathlib import Path

import pytest

from generate_asset_snapshot.db import get_connection, init_db
from generate_asset_snapshot.precompute import precompute_market


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    init_db(p)
    conn = get_connection(p)
    # Insert index price history (20 rows per symbol)
    for i in range(20):
        d = f"2025-01-{i + 1:02d}"
        conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", ("^GSPC", d, 5900 + i * 10))
        conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", ("^NDX", d, 21000 + i * 50))
    conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", ("CNY=X", "2025-01-20", 7.25))
    conn.commit()
    conn.close()
    return p


def test_precompute_market_writes_rows(db_path: Path) -> None:
    precompute_market(db_path)
    conn = get_connection(db_path)
    rows = conn.execute("SELECT * FROM computed_market").fetchall()
    conn.close()
    tickers = {r[0] for r in rows}
    assert "^GSPC" in tickers
    assert "^NDX" in tickers
    assert "__usdCny" in tickers


def test_precompute_market_sparkline_is_json(db_path: Path) -> None:
    import json
    precompute_market(db_path)
    conn = get_connection(db_path)
    row = conn.execute("SELECT sparkline FROM computed_market WHERE ticker='^GSPC'").fetchone()
    conn.close()
    data = json.loads(row[0])
    assert isinstance(data, list)
    assert len(data) == 20


def test_precompute_market_returns(db_path: Path) -> None:
    precompute_market(db_path)
    conn = get_connection(db_path)
    row = conn.execute("SELECT current, month_return, ytd_return FROM computed_market WHERE ticker='^GSPC'").fetchone()
    conn.close()
    assert row[0] == 6090  # 5900 + 19*10
    assert isinstance(row[1], float)
    assert isinstance(row[2], float)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd pipeline && .venv/Scripts/python -m pytest tests/unit/test_precompute_market.py -v`
Expected: FAIL — `precompute_market` not defined

- [ ] **Step 4: Implement precompute_market**

Add to `pipeline/generate_asset_snapshot/precompute.py`:

```python
import json
import os
from pathlib import Path

from .db import get_connection

INDEX_NAMES: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^NDX": "NASDAQ 100",
    "VXUS": "VXUS",
    "000300.SS": "CSI 300",
}


def precompute_market(db_path: Path) -> None:
    """Precompute market index returns and macro indicators into computed_market."""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_market")

        # Index data
        for ticker, name in INDEX_NAMES.items():
            rows = conn.execute(
                "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
                (ticker,),
            ).fetchall()
            if len(rows) < 2:
                continue
            closes = [r[1] for r in rows]
            dates = [r[0] for r in rows]
            current = closes[-1]

            month_idx = max(0, len(closes) - 23)
            month_return = round((current / closes[month_idx] - 1) * 100, 2)

            current_year = dates[-1][:4]
            ytd_start = next((c for d, c in zip(dates, closes, strict=False) if d.startswith(current_year)), closes[0])
            ytd_return = round((current / ytd_start - 1) * 100, 2)

            year_closes = closes[-252:]
            conn.execute(
                "INSERT INTO computed_market (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ticker, name, current, month_return, ytd_return, max(year_closes), min(year_closes), json.dumps(year_closes)),
            )

        # CNY rate as scalar indicator
        cny_row = conn.execute("SELECT close FROM daily_close WHERE symbol='CNY=X' ORDER BY date DESC LIMIT 1").fetchone()
        if cny_row:
            conn.execute(
                "INSERT INTO computed_market (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)"
                " VALUES ('__usdCny', '', ?, 0, 0, 0, 0, '[]')",
                (cny_row[0],),
            )

        # FRED macro indicators (if API key available)
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            try:
                from .market.fred import fetch_fred_data
                fred = fetch_fred_data(fred_key)
                if fred and "snapshot" in fred:
                    snap = fred["snapshot"]
                    for src, key in [("fedFundsRate", "__fedRate"), ("treasury10y", "__treasury10y"),
                                     ("cpiYoy", "__cpi"), ("unemployment", "__unemployment"), ("vix", "__vix")]:
                        if src in snap:
                            conn.execute(
                                "INSERT INTO computed_market (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)"
                                " VALUES (?, '', ?, 0, 0, 0, 0, '[]')",
                                (key, snap[src]),
                            )
            except Exception:
                pass

        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd pipeline && .venv/Scripts/python -m pytest tests/unit/test_precompute_market.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add pipeline/generate_asset_snapshot/db.py pipeline/generate_asset_snapshot/precompute.py pipeline/tests/unit/test_precompute_market.py
git commit -m "feat: precompute market index returns into computed_market table"
```

---

### Task 2: Pipeline — precompute holdings detail

**Files:**
- Modify: `pipeline/generate_asset_snapshot/db.py` (add table)
- Modify: `pipeline/generate_asset_snapshot/precompute.py`
- Test: `pipeline/tests/unit/test_precompute_market.py` (add tests)

- [ ] **Step 1: Add computed_holdings_detail table to schema**

In `pipeline/generate_asset_snapshot/db.py`, add to `_TABLES`:

```sql
CREATE TABLE IF NOT EXISTS computed_holdings_detail (
    ticker       TEXT PRIMARY KEY,
    month_return REAL NOT NULL DEFAULT 0,
    start_value  REAL NOT NULL DEFAULT 0,
    end_value    REAL NOT NULL DEFAULT 0,
    high_52w     REAL,
    low_52w      REAL,
    vs_high      REAL
);
```

- [ ] **Step 2: Write failing test**

Add to `pipeline/tests/unit/test_precompute_market.py`:

```python
from generate_asset_snapshot.precompute import precompute_holdings_detail


@pytest.fixture()
def holdings_db(tmp_path: Path) -> Path:
    p = tmp_path / "holdings.db"
    init_db(p)
    conn = get_connection(p)
    # Ticker with value on latest date
    conn.execute(
        "INSERT INTO computed_daily_tickers VALUES ('2025-01-20', 'VOO', 40000, 'US Equity', 'broad', 30000, 10000, 33.3)"
    )
    conn.execute(
        "INSERT INTO computed_daily_tickers VALUES ('2025-01-20', 'QQQM', 15000, 'US Equity', 'growth', 12000, 3000, 25.0)"
    )
    # Price history
    for i in range(20):
        d = f"2025-01-{i + 1:02d}"
        conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", ("VOO", d, 500 + i))
        conn.execute("INSERT INTO daily_close VALUES (?, ?, ?)", ("QQQM", d, 200 + i * 2))
    conn.commit()
    conn.close()
    return p


def test_precompute_holdings_writes_rows(holdings_db: Path) -> None:
    precompute_holdings_detail(holdings_db)
    conn = get_connection(holdings_db)
    rows = conn.execute("SELECT * FROM computed_holdings_detail").fetchall()
    conn.close()
    assert len(rows) == 2
    tickers = {r[0] for r in rows}
    assert "VOO" in tickers
    assert "QQQM" in tickers


def test_precompute_holdings_month_return(holdings_db: Path) -> None:
    precompute_holdings_detail(holdings_db)
    conn = get_connection(holdings_db)
    row = conn.execute("SELECT month_return, end_value FROM computed_holdings_detail WHERE ticker='VOO'").fetchone()
    conn.close()
    assert row[1] == 40000
    assert isinstance(row[0], float)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd pipeline && .venv/Scripts/python -m pytest tests/unit/test_precompute_market.py -v -k holdings`
Expected: FAIL — `precompute_holdings_detail` not defined

- [ ] **Step 4: Implement precompute_holdings_detail**

Add to `pipeline/generate_asset_snapshot/precompute.py`:

```python
def precompute_holdings_detail(db_path: Path) -> None:
    """Precompute per-ticker performance into computed_holdings_detail."""
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_holdings_detail")

        row = conn.execute("SELECT date FROM computed_daily_tickers ORDER BY date DESC LIMIT 1").fetchone()
        if row is None:
            conn.commit()
            return
        latest_date = row[0]

        ticker_rows = conn.execute(
            "SELECT ticker, value FROM computed_daily_tickers WHERE date = ? AND value > 0",
            (latest_date,),
        ).fetchall()

        real_tickers = {t: v for t, v in ticker_rows if t.isascii() and " " not in t and len(t) <= 5}

        for ticker, value in real_tickers.items():
            closes = conn.execute(
                "SELECT close FROM daily_close WHERE symbol = ? ORDER BY date", (ticker,),
            ).fetchall()
            if len(closes) < 2:
                continue
            prices = [r[0] for r in closes]
            current = prices[-1]

            month_idx = max(0, len(prices) - 23)
            month_ret = round((current / prices[month_idx] - 1) * 100, 2)
            start_value = round(value / (1 + month_ret / 100), 2) if month_ret != -100 else 0.0

            year_prices = prices[-252:]
            high = max(year_prices)
            low = min(year_prices)
            vs_high = round((current / high - 1) * 100, 2)

            conn.execute(
                "INSERT INTO computed_holdings_detail (ticker, month_return, start_value, end_value, high_52w, low_52w, vs_high)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (ticker, month_ret, start_value, round(value, 2), high, low, vs_high),
            )

        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 5: Run tests**

Run: `cd pipeline && .venv/Scripts/python -m pytest tests/unit/test_precompute_market.py -v`
Expected: 5 passed

- [ ] **Step 6: Wire into build_timemachine_db.py**

In `pipeline/scripts/build_timemachine_db.py`, add after the existing `_compute_and_store_prefix` call in `_full_build`:

```python
from generate_asset_snapshot.precompute import precompute_market, precompute_holdings_detail

print("[M] Precomputing market + holdings detail...")
precompute_market(DB_PATH)
precompute_holdings_detail(DB_PATH)
```

Add the same two lines in `_incremental_build` after the prefix computation.

- [ ] **Step 7: Run full pipeline tests**

Run: `cd pipeline && .venv/Scripts/python -m pytest -q`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add pipeline/generate_asset_snapshot/db.py pipeline/generate_asset_snapshot/precompute.py pipeline/scripts/build_timemachine_db.py pipeline/tests/unit/test_precompute_market.py
git commit -m "feat: precompute holdings detail into computed_holdings_detail table"
```

---

### Task 3: D1 schema + views + sync script

**Files:**
- Create: `worker/wrangler.toml`
- Create: `worker/schema.sql`
- Create: `pipeline/scripts/sync_to_d1.py`

- [ ] **Step 1: Create D1 database**

```bash
npx wrangler d1 create portal-db
```

Note the database ID from the output.

- [ ] **Step 2: Create wrangler.toml**

Create `worker/wrangler.toml`:

```toml
name = "portal-api"
main = "src/index.ts"
compatibility_date = "2024-12-01"

[[d1_databases]]
binding = "DB"
database_name = "portal-db"
database_id = "<paste-id-from-step-1>"
```

- [ ] **Step 3: Create schema.sql with tables + views**

Create `worker/schema.sql`:

```sql
-- Tables (subset needed by Worker — excludes empower/qianji_balances)
CREATE TABLE IF NOT EXISTS computed_daily (
    date TEXT PRIMARY KEY, total REAL NOT NULL,
    us_equity REAL NOT NULL, non_us_equity REAL NOT NULL,
    crypto REAL NOT NULL, safe_net REAL NOT NULL,
    liabilities REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS computed_prefix (
    date TEXT PRIMARY KEY, income REAL NOT NULL DEFAULT 0,
    expenses REAL NOT NULL DEFAULT 0, buys REAL NOT NULL DEFAULT 0,
    sells REAL NOT NULL DEFAULT 0, dividends REAL NOT NULL DEFAULT 0,
    net_cash_in REAL NOT NULL DEFAULT 0, cc_payments REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS computed_daily_tickers (
    date TEXT NOT NULL, ticker TEXT NOT NULL,
    value REAL NOT NULL, category TEXT NOT NULL DEFAULT '',
    subtype TEXT NOT NULL DEFAULT '', cost_basis REAL NOT NULL DEFAULT 0,
    gain_loss REAL NOT NULL DEFAULT 0, gain_loss_pct REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, ticker)
);
CREATE TABLE IF NOT EXISTS fidelity_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, run_date TEXT NOT NULL,
    account TEXT NOT NULL, account_number TEXT NOT NULL,
    action TEXT NOT NULL, symbol TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '', lot_type TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL DEFAULT 0, price REAL NOT NULL DEFAULT 0,
    amount REAL NOT NULL DEFAULT 0, settlement_date TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS qianji_transactions (
    date TEXT NOT NULL, type TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '', amount REAL NOT NULL,
    account TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS computed_market (
    ticker TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '',
    current REAL NOT NULL DEFAULT 0, month_return REAL NOT NULL DEFAULT 0,
    ytd_return REAL NOT NULL DEFAULT 0, high_52w REAL NOT NULL DEFAULT 0,
    low_52w REAL NOT NULL DEFAULT 0, sparkline TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS computed_holdings_detail (
    ticker TEXT PRIMARY KEY, month_return REAL NOT NULL DEFAULT 0,
    start_value REAL NOT NULL DEFAULT 0, end_value REAL NOT NULL DEFAULT 0,
    high_52w REAL, low_52w REAL, vs_high REAL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_daily_tickers_date ON computed_daily_tickers(date);
CREATE INDEX IF NOT EXISTS idx_fidelity_date ON fidelity_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_qianji_txn_date ON qianji_transactions(date);

-- camelCase views
CREATE VIEW IF NOT EXISTS v_daily AS
SELECT date, total, us_equity AS usEquity, non_us_equity AS nonUsEquity,
  crypto, safe_net AS safeNet, liabilities
FROM computed_daily ORDER BY date;

CREATE VIEW IF NOT EXISTS v_prefix AS
SELECT date, income, expenses, buys, sells, dividends,
  net_cash_in AS netCashIn, cc_payments AS ccPayments
FROM computed_prefix ORDER BY date;

CREATE VIEW IF NOT EXISTS v_daily_tickers AS
SELECT date, ticker, value, category, subtype,
  cost_basis AS costBasis, gain_loss AS gainLoss, gain_loss_pct AS gainLossPct
FROM computed_daily_tickers ORDER BY date, value DESC;

CREATE VIEW IF NOT EXISTS v_fidelity_txns AS
SELECT run_date AS runDate, action, symbol, amount
FROM fidelity_transactions ORDER BY id;

CREATE VIEW IF NOT EXISTS v_qianji_txns AS
SELECT date, type, category, amount
FROM qianji_transactions ORDER BY date;

CREATE VIEW IF NOT EXISTS v_market AS
SELECT ticker, name, current, month_return AS monthReturn,
  ytd_return AS ytdReturn, high_52w AS high52w, low_52w AS low52w, sparkline
FROM computed_market ORDER BY ticker;

CREATE VIEW IF NOT EXISTS v_holdings_detail AS
SELECT ticker, month_return AS monthReturn, start_value AS startValue,
  end_value AS endValue, high_52w AS high52w, low_52w AS low52w, vs_high AS vsHigh
FROM computed_holdings_detail ORDER BY month_return DESC;
```

- [ ] **Step 4: Apply schema to D1**

```bash
cd worker && npx wrangler d1 execute portal-db --remote --file=schema.sql
```

- [ ] **Step 5: Create sync_to_d1.py**

Create `pipeline/scripts/sync_to_d1.py`:

```python
"""Sync local SQLite timemachine.db to Cloudflare D1 via wrangler CLI."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = PIPELINE_DIR / "data" / "timemachine.db"
WORKER_DIR = PIPELINE_DIR.parent / "worker"

TABLES_TO_SYNC = [
    "computed_daily",
    "computed_prefix",
    "computed_daily_tickers",
    "fidelity_transactions",
    "qianji_transactions",
    "computed_market",
    "computed_holdings_detail",
]


def _dump_table(conn: sqlite3.Connection, table: str) -> list[str]:
    """Generate INSERT statements for a table."""
    cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608
    cols = [d[0] for d in cursor.description]
    col_list = ", ".join(cols)
    stmts: list[str] = [f"DELETE FROM {table};"]
    for row in cursor:
        vals = ", ".join(
            "NULL" if v is None else f"'{str(v).replace(chr(39), chr(39)+chr(39))}'" if isinstance(v, str) else str(v)
            for v in row
        )
        stmts.append(f"INSERT INTO {table} ({col_list}) VALUES ({vals});")
    return stmts


def main() -> None:
    if not DB_PATH.exists():
        print(f"ERROR: {DB_PATH} not found")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    all_stmts: list[str] = []

    for table in TABLES_TO_SYNC:
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
        print(f"  {table}: {count} rows")
        all_stmts.extend(_dump_table(conn, table))

    conn.close()

    # Write to temp file
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sql", delete=False, encoding="utf-8") as f:
        f.write("\n".join(all_stmts))
        dump_path = f.name

    print(f"\n  Dump: {len(all_stmts)} SQL statements -> {dump_path}")

    # Execute via wrangler
    print("  Uploading to D1...")
    result = subprocess.run(
        ["npx", "wrangler", "d1", "execute", "portal-db", "--remote", f"--file={dump_path}"],
        cwd=WORKER_DIR,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  ERROR: {result.stderr}")
        sys.exit(1)
    print("  Done!")

    Path(dump_path).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Test sync locally**

```bash
cd pipeline && python scripts/sync_to_d1.py
```

Expected: tables synced, row counts printed, "Done!" at end.

- [ ] **Step 7: Commit**

```bash
git add worker/wrangler.toml worker/schema.sql pipeline/scripts/sync_to_d1.py
git commit -m "feat: D1 schema, views, and sync script"
```

---

### Task 4: Worker implementation

**Files:**
- Create: `worker/src/index.ts`
- Create: `worker/package.json`
- Create: `worker/tsconfig.json`

- [ ] **Step 1: Create worker/package.json**

```json
{
  "name": "portal-api",
  "private": true,
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy"
  },
  "devDependencies": {
    "@cloudflare/workers-types": "^4",
    "typescript": "^5",
    "wrangler": "^4"
  }
}
```

- [ ] **Step 2: Create worker/tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ESNext",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "lib": ["ESNext"],
    "types": ["@cloudflare/workers-types"],
    "strict": true,
    "noEmit": true
  },
  "include": ["src"]
}
```

- [ ] **Step 3: Install deps**

```bash
cd worker && npm install
```

- [ ] **Step 4: Create worker/src/index.ts**

```ts
interface Env {
  DB: D1Database;
}

const CORS_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: CORS_HEADERS });
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

    // Split market: indices vs __scalar indicators
    const indices: Record<string, unknown>[] = [];
    const meta: Record<string, number | null> = {
      fedRate: null, treasury10y: null, cpi: null,
      unemployment: null, vix: null, dxy: null,
      usdCny: null, goldReturn: null, btcReturn: null,
      portfolioMonthReturn: null,
    };
    for (const r of market.results as Record<string, unknown>[]) {
      const ticker = r.ticker as string;
      if (ticker.startsWith("__")) {
        meta[ticker.slice(2)] = r.current as number;
      } else {
        indices.push({ ...r, sparkline: JSON.parse(r.sparkline as string) });
      }
    }

    // Top 5 / bottom 5 (already sorted DESC by view)
    const all = holdings.results as Record<string, unknown>[];
    const holdingsDetail = {
      topPerformers: all.slice(0, 5),
      bottomPerformers: all.length > 5 ? all.slice(-5).reverse() : [],
      upcomingEarnings: [],
    };

    return Response.json(
      {
        daily: daily.results,
        prefix: prefix.results,
        dailyTickers: tickers.results,
        fidelityTxns: fidelity.results,
        qianjiTxns: qianji.results,
        market: { indices, ...meta },
        holdingsDetail,
      },
      {
        headers: {
          ...CORS_HEADERS,
          "Cache-Control": "public, max-age=3600",
        },
      },
    );
  },
} satisfies ExportedHandler<Env>;
```

- [ ] **Step 5: Type check**

```bash
cd worker && npx tsc --noEmit
```

Expected: no errors

- [ ] **Step 6: Test locally with wrangler dev**

```bash
cd worker && npx wrangler dev --remote
```

Then: `curl http://localhost:8787/timeline | python -c "import sys,json; d=json.load(sys.stdin); print(list(d.keys())); print(len(d['daily']), 'daily rows')"` 

Expected: `['daily', 'prefix', 'dailyTickers', 'fidelityTxns', 'qianjiTxns', 'market', 'holdingsDetail']`

- [ ] **Step 7: Deploy**

```bash
cd worker && npx wrangler deploy
```

Note the deployed URL (e.g. `https://portal-api.<account>.workers.dev`).

- [ ] **Step 8: Commit**

```bash
git add worker/
git commit -m "feat: Cloudflare Worker — GET /timeline from D1"
```

---

### Task 5: Frontend config + CI

**Files:**
- Modify: `.env.local`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Update .env.local**

Add the Worker URL for local dev (keep both so local FastAPI still works):

```
NEXT_PUBLIC_TIMELINE_URL=https://portal-api.<account>.workers.dev/timeline
```

- [ ] **Step 2: Update CI to deploy Worker**

Add to `.github/workflows/ci.yml`, in the frontend job after Pages deploy:

```yaml
      - name: Deploy Worker
        if: github.ref == 'refs/heads/main' && github.event_name == 'push'
        run: cd worker && npm ci && npx wrangler deploy
        env:
          CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_API_TOKEN }}
          CLOUDFLARE_ACCOUNT_ID: ${{ secrets.CLOUDFLARE_ACCOUNT_ID }}
```

Update the build step to use Worker URL:

```yaml
      - run: npx next build
        env:
          NEXT_PUBLIC_R2_URL: ${{ secrets.NEXT_PUBLIC_R2_URL }}
          NEXT_PUBLIC_TIMELINE_URL: ${{ secrets.NEXT_PUBLIC_TIMELINE_URL }}
```

Add `NEXT_PUBLIC_TIMELINE_URL` as a GitHub Actions secret pointing to the Worker URL.

- [ ] **Step 3: Build and verify frontend works with Worker**

```bash
npx next build && npx next start
```

Open http://localhost:3000/finance — should load data from the Worker.

- [ ] **Step 4: Commit**

```bash
git add .env.local .github/workflows/ci.yml
git commit -m "feat: point frontend to Worker, add Worker deploy to CI"
```

---

### Task 6: Verification

**Files:** none (verification only)

- [ ] **Step 1: Compare Worker response vs FastAPI response**

```bash
# Start local FastAPI
cd pipeline && .venv/Scripts/python -m generate_asset_snapshot.server &

# Fetch both
curl -s http://localhost:8000/timeline > /tmp/fastapi.json
curl -s https://portal-api.<account>.workers.dev/timeline > /tmp/worker.json

# Compare key structure and row counts
python -c "
import json
a = json.load(open('/tmp/fastapi.json'))
b = json.load(open('/tmp/worker.json'))
for k in a:
    if isinstance(a[k], list):
        print(f'{k}: fastapi={len(a[k])} worker={len(b.get(k,[]))}')
    else:
        print(f'{k}: fastapi={type(a[k]).__name__} worker={type(b.get(k)).__name__}')
"
```

Expected: row counts match for all arrays, market/holdingsDetail are dicts.

- [ ] **Step 2: Run E2E tests against Worker**

```bash
NEXT_PUBLIC_TIMELINE_URL=https://portal-api.<account>.workers.dev/timeline npx next build
npx playwright test e2e/finance.spec.ts
```

Expected: all pass

- [ ] **Step 3: Final commit + PR**

```bash
git push origin main
```
