# R2 JSON Migration Design Note - 2026-05-02

## Context

The current production data path is:

```text
Python ETL -> local SQLite timemachine.db -> D1 sync -> D1 views -> Worker SELECT -> frontend Zod -> UI compute
```

This works, but D1 introduces a large amount of operational code whose main job is protecting a mutable production database from destructive sync mistakes:

- range/full/diff sync policy
- prod parity checks
- D1 schema/view generation and drift tests
- Worker SQL adapter logic
- real-worker D1 e2e
- D1 backup workflow

The dashboard data is effectively a read-only snapshot after each ETL run. That makes object storage a plausible better fit than an online SQL database.

## Proposed Architecture

Replace D1 with versioned JSON snapshots stored in Cloudflare R2, while keeping a thin Worker as the private API facade for manifest resolution, R2 object streaming, cache/error handling, Cloudflare Access, and API compatibility.

```text
Raw data
-> Python ETL
-> local SQLite timemachine.db
-> validation / regression / positions verify
-> export versioned JSON
-> upload to R2
-> thin Worker API facade
-> Next static frontend
```

The frontend API surface should stay unchanged:

```text
GET /api/timeline
GET /api/econ
GET /api/prices/:symbol
```

Only the Worker implementation changes:

```text
current: Worker -> D1 SELECTs -> JSON response
new:     Worker -> R2 artifacts -> JSON response
```

## Decision

Use **Path B2: R2 JSON snapshots with manifest-last publication** as the recommended direction.

This is not "minimal R2" and not "dump JSON somewhere and hope the Worker reads it." The correctness bar is:

```text
data publication correctness must not regress;
any simplification that removes a current guard must replace it with an equal or stronger hard gate.
```

B2 means:

- local SQLite remains the source/build/debug database and SQL investigation surface
- production serving data becomes validated JSON artifacts in R2
- the Worker keeps the same public API but reads through a manifest pointer
- row counts, hashes, schema parsing, and D1-vs-R2 parity are hard gates before cutover
- `manifest.json` is updated last, after every referenced object is uploaded and verified
- old R2 snapshots are retained for manifest rollback

Rejected variants:

- **naive full D1**: simpler sync, but loses too much drift/shortfall/blast-radius protection.
- **minimal R2**: simpler Worker path, but correctness regresses unless manifest, row counts, hashes, and parity gates are added.
- **Path A as the main plan**: keeps production SQL, but after adding fingerprints and shortfall protection it is no longer a decisive simplicity win; it still leaves a mutable production DB and a sync state machine.

## Alternative: Simplified D1 Mirror

There is a smaller fallback path that keeps D1 and avoids the R2 exporter/manifest model. It is useful only if production-side SQL becomes a hard requirement again.

```text
Raw data
-> Python ETL
-> local SQLite timemachine.db
-> validation / regression / positions verify
-> mirror local DB into D1
-> existing Worker SELECTs
-> Next static frontend
```

Sketch:

```text
daily_close:
  normal: refresh-window date-keyed upsert
  auto-upgrade: full replace all prices if historical price fingerprint changed
  manual override: full replace all prices, or selected symbols

large derived daily tables:
  normal: refresh-window date-keyed upsert
  auto-upgrade: full replace derived tables if historical derived fingerprint changed
  manual override: full replace derived tables

small/source/read-model tables:
  full replace from local SQLite
```

Path A keeps production SQL, but correctness-neutral simplification requires a shortfall guard, historical fingerprints, sync metadata, build-layer stale-data detection, and post-sync smoke checks. That moves complexity more than it removes it. Since local SQLite is sufficient for SQL/debugging, Path A is not the implementation plan.

Do not use `append-IGNORE` for broker transactions under this fallback unless stable natural keys are first introduced and proven to preserve legitimate duplicate rows.

## R2 Object Layout

Two trees with different semantics: a versioned, immutable snapshot tree for ETL-owned artifacts, and an unversioned mutable date-keyed cache tree for price series.

```text
r2://portal-data/
  manifest.json                              # pointer for active ETL snapshot
  snapshots/2026-05-02T120000Z/
    timeline.json
    econ.json
    price-state.json                          # ETL-owned fetch state for price cron
    price-txns/
      _index.json                            # symbols with transaction marker files
      VOO.json                               # {symbol, transactions}
      SPY.json
      FXAIX.json
  prices/                                    # mutable price-series cache, NOT under manifest
    _index.json                              # {symbolKey: {symbol, maxDate}}
    series/
      VOO.json                               # {symbol, prices}
      SPY.json
      FXAIX.json
```

Publication order matters because R2 does not provide a multi-object transaction:

1. Export JSON locally.
2. Validate JSON locally.
3. Upload immutable snapshot objects.
4. Read back key objects and verify hashes/sizes.
5. Update `manifest.json` last.

The manifest acts as the atomic publish pointer. Users should never read a half-published snapshot if the Worker always resolves data through the manifest.

## Prices: Split Snapshot Markers From Mutable Series

Prices have two independent data lifecycles. Keep them as two object families with a single writer each:

- `snapshots/<version>/price-txns/<symbolKey>.json` — ETL-owned immutable transaction markers for the active snapshot.
- `prices/series/<symbolKey>.json` — price-cron-owned mutable date-keyed close series.

The Worker assembles `GET /api/prices/:symbol` from those two small objects:

```json
{
  "symbol": "VOO",
  "prices": [
    { "date": "2026-05-01", "close": 512.34 }
  ],
  "transactions": [
    { "runDate": "2025-01-15", "actionType": "buy", "quantity": 1.23, "price": 480.0, "amount": 590.4 }
  ]
}
```

This is the one intentional exception to pure object streaming. It avoids the worse design where ETL and the nightly price cron both mutate one full `prices/<symbol>.json` endpoint payload. The split makes ownership mechanical:

- ETL never overwrites existing remote `prices/series/*` during normal publish.
- the nightly price job never writes `snapshots/*`.
- if a new symbol appears, the publisher may bootstrap a missing `prices/series/<symbolKey>.json` and add its `_index.json` entry from local SQLite before manifest switch; replacing an existing series is an explicit repair operation, not normal publish behavior.

Forcing the close series into the versioned snapshot tree would create needless copy churn: every snapshot would have to copy ~84 mostly unchanged price files, and the prices cron would have to publish a new manifest just to append recent closes.

### Symbol keys

Endpoint symbols remain normal ticker strings in JSON payloads, but object keys must be deterministic and path-safe:

```text
request path      /api/prices/VOO
canonical symbol  decodeURIComponent(path segment).toUpperCase()
symbolKey         encodeURIComponent(canonical symbol)
R2 keys           prices/series/<symbolKey>.json
                  snapshots/<version>/price-txns/<symbolKey>.json
```

`payload.symbol` stays as the canonical symbol, not the encoded key. This handles unusual symbols without leaking path parsing rules into data payloads.

`prices/_index.json` should also be keyed by `symbolKey` and store the canonical symbol plus `maxDate`. The Worker can use the index to distinguish an unknown symbol from a missing known series object.

### yfinance call count must not change

The current nightly script is the constraint to preserve:

```text
SELECT symbol, MAX(date) FROM daily_close GROUP BY symbol   # state read
→ compute per-symbol gap (today - max_date)
→ yfinance fetch only the gap
→ INSERT OR IGNORE
```

After migration the algorithm is identical, only the state reads change:

```text
GET manifest.json
GET snapshots/<version>/price-state.json                     # active ETL-owned fetch state
GET prices/_index.json                                       # current maxDate by symbolKey
→ compute per-symbol gap
→ yfinance fetch only the gap
→ for each symbol with new rows: GET prices/series/SYMBOL_KEY.json, merge prices by date, PUT back
→ PUT prices/_index.json
```

yfinance call count is **identical to today** — gap-only, per-symbol. R2 traffic per nightly run adds two small metadata GETs, then roughly: 1 GET on `prices/_index.json`, K GETs + K PUTs on changed series files (K is approximately the active symbol set on weekdays, 0 on weekends), 1 PUT on `prices/_index.json`. Each symbol file update must be an idempotent date-keyed merge of the `prices` array, not blind append, because recent Yahoo closes and split-adjusted history can be revised. Free tier (1M Class A / 10M Class B per month) is not in danger.

### Closed-position grace coupling

`sync_prices_nightly.py` currently reads `fidelity_transactions` from D1 to reconstruct `{symbol: (firstBuy, lastSell)}` and stop fetching ~7 days after a position is fully closed. After D1 is removed, that state has to come from somewhere.

Have the ETL exporter emit a small `price-state.json` artifact inside the active snapshot:

```json
{
  "VOO":  { "symbol": "VOO",  "firstBuy": "2022-03-14", "lastSell": null,         "maxDate": "2026-05-01" },
  "TSLA": { "symbol": "TSLA", "firstBuy": "2021-09-02", "lastSell": "2024-11-08", "maxDate": "2024-11-15" }
}
```

`price-state.json` is keyed by `symbolKey`, with the canonical symbol repeated in the value. The prices cron reads `manifest.json`, then the active `price-state.json`. That keeps the state consistent with the active transaction snapshot without pulling and parsing the full 4.6 MB `timeline.json`. It also keeps `prices/` exclusively owned by the price updater, while all ETL outputs remain versioned and manifest-protected.

## Why Keep The Worker

Keep the Worker. Do not expose the R2 bucket or object layout directly to the browser.

The reason is not merely same-origin access. The Worker keeps the production contract narrow:

- R2 remains private; personal finance JSON is not directly public bucket content.
- Cloudflare Access/auth stays at the API boundary.
- The frontend keeps `/api/timeline`, `/api/econ`, and `/api/prices/:symbol`.
- Manifest lookup stays server-side instead of leaking object paths into the browser.
- Cache headers, missing-object errors, and stale-manifest errors are handled in one place.
- Rollback can be controlled through the manifest or Worker config without changing frontend code.
- The public endpoint can switch from D1 to R2 without changing the frontend.

The Worker must stay thin:

```text
timeline/econ request -> route -> manifest lookup -> R2 get -> stream Response
prices request        -> route -> manifest lookup -> R2 get series + txns -> assemble small Response
```

It should not parse and re-stringify `timeline.json` or `econ.json` on the hot path, reshape large payloads, run business logic, run SQL, or perform runtime Zod validation. The small `/prices/:symbol` assembly exists only to preserve single-writer ownership of price series and transaction markers. The data contract is enforced before publication, not inside the request path.

## Implementation Design

The implementation should be split into small components with one clear responsibility each. The steady-state production path should have no D1 dependency.

### Data correctness invariants

These are non-negotiable. Implementation is allowed to be simple, but it must preserve these invariants:

1. **Single source of truth:** every production artifact is exported from the same local `timemachine.db` that passed regression gates.
2. **Shape compatibility:** timeline/econ artifacts are produced from the same SQLite view projections used by the current D1 Worker where views exist; price transaction markers are produced from the same source query as the current price endpoint.
3. **Manifest-last publication:** `manifest.json` is the only active snapshot pointer for timeline/econ. It is written only after every referenced snapshot object has been uploaded and verified.
4. **No partial active snapshot:** the Worker must resolve timeline/econ objects only through the active manifest. It must never list a snapshot directory and infer "latest".
5. **Write-once snapshots:** objects under `snapshots/<version>/` are immutable by convention. A failed publish creates a new version on retry instead of overwriting a previous version.
6. **Row-count guard:** manifest row counts must match SQLite source-view row counts before upload and after upload verification.
7. **Hash guard:** manifest `sha256` and `bytes` must match local files and R2 read-back bytes before the manifest is published.
8. **Schema guard:** exported JSON must parse with the frontend schemas before upload. Schema validation is a publish-time gate, not a Worker hot-path cost.
9. **Bounded cache staleness only:** endpoint caching may serve the previous active manifest for a short TTL, but missing manifests or referenced objects must return explicit 5xx errors. The Worker must not silently fall back to an older object unless rollback was explicitly requested by changing the manifest or reverting the Worker build.
10. **Single-writer price ownership:** ETL owns `snapshots/<version>/price-state.json` and `snapshots/<version>/price-txns/*`; the nightly price job owns ongoing mutation of `prices/_index.json` and `prices/series/*`. The publisher may only bootstrap missing series/index entries for new symbols before manifest switch, or perform an explicit repair.
11. **Price cache isolation:** mutable `prices/` objects are outside the timeline/econ manifest. A bad price-series update must not change the active timeline/econ snapshot or transaction markers.

### DB-to-artifact transformation

The exporter does not dump the SQLite database. It materializes the exact API payloads that the Worker currently assembles from D1.

Local `timemachine.db` already contains the camelCase projection views from `pipeline/etl/db.py::_VIEWS`; `init_db()` creates those views for local SQLite, and `worker/schema.sql` mirrors them into D1. The exporter should open the local DB read-only, query those views, and write endpoint-shaped JSON.

`timeline.json` is assembled as:

```text
daily                 = SELECT * FROM v_daily
dailyTickers          = SELECT * FROM v_daily_tickers
fidelityTxns          = SELECT * FROM v_fidelity_txns
qianjiTxns            = SELECT * FROM v_qianji_txns
robinhoodTxns         = SELECT * FROM v_robinhood_txns
empowerContributions  = SELECT * FROM v_empower_contributions
categories            = SELECT * FROM v_categories
market                = { indices: SELECT * FROM v_market_indices }
holdingsDetail        = SELECT * FROM v_holdings_detail
syncMeta              = { backend: "r2", version, last_sync: generatedAt }
errors                = {}
```

Important difference from the current runtime Worker: the exporter should fail closed. The D1 Worker currently fail-opens optional sections because a live production query can fail independently. During offline export, any query failure is a build/publish failure. Do not encode exporter failures as `errors` in the published artifact.

Minimum timeline gates:

- `daily` must be non-empty.
- `categories` must be non-empty.
- all expected view queries must succeed.
- output must parse with `TimelineDataSchema`.
- `syncMeta` must remain a `Record<string, string>` to match the current schema.
- migration parity may normalize `syncMeta` because D1 and R2 publish metadata differ; financial data sections must match exactly.

`econ.json` is assembled as:

```text
generatedAt = manifest.generatedAt
snapshot    = object from SELECT key, value FROM v_econ_snapshot
series      = object from SELECT key, points FROM v_econ_series_grouped
```

Keep `series[key]` as the SQLite JSON string produced by `json_group_array`, matching the current API. The frontend `EconDataSchema` already accepts and parses that string. Migration parity may normalize `generatedAt`; values inside `snapshot` and `series` must match exactly.

Price-related artifacts are split by writer:

```text
snapshots/<version>/price-txns/<symbolKey>.json
  symbol       = canonical symbol
  transactions = SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount
                 FROM fidelity_transactions
                 WHERE symbol = :symbol
                 ORDER BY id

snapshots/<version>/price-state.json
  keyed by symbolKey
  symbol       = canonical symbol
  firstBuy     = first buy date from transaction history
  lastSell     = last sell date when fully closed, otherwise null
  maxDate      = MAX(date) from daily_close for that symbol

prices/series/<symbolKey>.json
  symbol       = canonical symbol
  prices       = SELECT date, close
                 FROM daily_close
                 WHERE symbol = :symbol
                 ORDER BY date
```

The exporter should generate transaction-marker files only for symbols with transaction markers. A missing marker file means `transactions: []`.

The publisher should ensure a price-series file exists for every active symbol in `price-state.json`. For a new symbol, it may bootstrap the missing `prices/series/<symbolKey>.json` from local SQLite before switching the manifest. For existing remote series, normal publish must not overwrite the cron-owned file.

### Artifact contract

Use a local artifact directory that mirrors the production R2 key layout:

```text
pipeline/artifacts/r2/
  manifest.json
  snapshots/<version>/timeline.json
  snapshots/<version>/econ.json
  snapshots/<version>/price-state.json
  snapshots/<version>/price-txns/_index.json
  snapshots/<version>/price-txns/<symbolKey>.json
  prices/_index.json
  prices/series/<symbolKey>.json                # only for bootstrap or explicit repair
  reports/
    export-summary.json
    parity-summary.json
```

The production R2 layout remains:

```text
r2://portal-data/
  manifest.json
  snapshots/<version>/timeline.json
  snapshots/<version>/econ.json
  snapshots/<version>/price-state.json
  snapshots/<version>/price-txns/_index.json
  snapshots/<version>/price-txns/<symbolKey>.json
  prices/_index.json
  prices/series/<symbolKey>.json
```

`manifest.json` should be explicit enough to serve as the publish receipt:

```json
{
  "schemaVersion": 1,
  "version": "2026-05-02T170000Z",
  "generatedAt": "2026-05-02T17:00:00Z",
  "source": {
    "gitCommit": "abc1234",
    "sqlitePath": "pipeline/data/timemachine.db",
    "latestDate": "2026-05-01"
  },
  "objects": {
    "timeline": {
      "key": "snapshots/2026-05-02T170000Z/timeline.json",
      "sha256": "...",
      "bytes": 4600000,
      "contentType": "application/json"
    },
    "econ": {
      "key": "snapshots/2026-05-02T170000Z/econ.json",
      "sha256": "...",
      "bytes": 120000,
      "contentType": "application/json"
    },
    "priceState": {
      "key": "snapshots/2026-05-02T170000Z/price-state.json",
      "sha256": "...",
      "bytes": 9000,
      "contentType": "application/json"
    },
    "priceTxnIndex": {
      "key": "snapshots/2026-05-02T170000Z/price-txns/_index.json",
      "sha256": "...",
      "bytes": 2000,
      "contentType": "application/json"
    }
  },
  "priceTxns": {
    "VOO": {
      "symbol": "VOO",
      "key": "snapshots/2026-05-02T170000Z/price-txns/VOO.json",
      "sha256": "...",
      "bytes": 5000,
      "rows": 12,
      "contentType": "application/json"
    }
  },
  "rowCounts": {
    "daily": 1234,
    "dailyTickers": 45678,
    "fidelityTxns": 123,
    "qianjiTxns": 4567,
    "robinhoodTxns": 123,
    "empowerContributions": 42,
    "categories": 8,
    "marketIndices": 4000,
    "holdingsDetail": 50,
    "econSeries": 20,
    "econSnapshot": 10,
    "priceTxnSymbols": 20,
    "priceTxnRows": 120
  }
}
```

Do not include hashes that require the Worker to re-read and hash object bodies on every request. The Worker can trust a published manifest because the publisher already verified it; request-time verification should be existence/content-type/streaming only.

Keys in `priceTxns` are `symbolKey` values, not necessarily raw ticker strings.

Do not put `prices/series/*` in the snapshot manifest. Those files are mutable price-cron state. Their correctness is guarded by the price job's date-keyed merge, schema parse, and `_index.json` update; publisher-side writes are limited to missing-symbol bootstrap or explicit repair.

### Component boundaries

Suggested implementation components:

```text
pipeline/scripts/r2_artifacts.py
  subcommands:
    export   -- read SQLite views, write JSON files, write manifest, write export summary
    verify   -- row-count check, sha256/bytes check, latest-date check, schema check wrapper
    publish  -- upload objects, read back and verify, upload manifest last
  modes:
    --local  -- publish to Miniflare/local R2
    --remote -- publish to production R2

scripts/validate_r2_artifacts_zod.ts
  input:  artifact directory
  does:   run the existing frontend Zod schemas against generated JSON

pipeline/scripts/migration/compare_d1_r2_payloads.py
  input:  current D1 Worker URL, local/preview R2 Worker URL
  does:   migration-only canonical parity diff
  lifetime: delete after cutover confidence is established

worker/src/index.ts
  does:   route, manifest lookup, R2 get, stream timeline/econ, assemble small prices response, cache/error headers
  does not: SQL, JSON reshape, business compute, runtime Zod
```

Prefer one Python CLI over several near-identical scripts. The ownership boundaries still matter: export, verification, publication, migration parity, and runtime serving should stay separate even if the first three are subcommands in one file.

### Worker behavior

Routes should preserve the current public API:

```text
GET /api/timeline      -> manifest.objects.timeline.key
GET /api/econ          -> manifest.objects.econ.key
GET /api/prices/:sym   -> prices/series/<symbolKey>.json
                         + optional manifest.priceTxns[symbolKey]
```

Required behavior:

- Strip the optional `/api` prefix exactly as today.
- Fetch `manifest.json` for timeline/econ and prices; cache it briefly.
- Stream `timeline.json` and `econ.json` object bodies directly when possible.
- For `/prices/:symbol`, decode the path segment, uppercase the symbol, compute `symbolKey`, read cached `prices/_index.json`, read the price series when the index lists it, read the active snapshot transaction-marker file only if `manifest.priceTxns` lists it, and return `{ symbol, prices, transactions }`.
- If neither the price index nor `manifest.priceTxns` knows the symbol, return the current SQL-compatible empty payload. If an index/manifest-referenced object is missing, return an explicit error.
- Preserve current cache TTL intent: timeline around 60s, econ around 600s, prices around 300s unless implementation finds a better existing constant.
- Return explicit errors for missing manifest, missing referenced object, or malformed route.
- Do not parse `timeline.json` or `econ.json` on the hot path.

### Cache and ETag strategy

`manifest.json` is the only mutable pointer for snapshot data. Do not cache it aggressively. The Worker may cache it for no longer than the shortest endpoint TTL, or skip edge caching for the manifest and rely on endpoint response caching.

Immutable snapshot objects may be uploaded with long-lived metadata such as:

```text
Cache-Control: public, max-age=31536000, immutable
```

User-facing endpoint responses should keep the current effective TTL intent instead of exposing snapshot-object cache headers directly:

```text
/api/timeline         ~60s
/api/econ             ~600s
/api/prices/:symbol   ~300s
```

Set deterministic ETags for endpoint responses even if conditional `304 Not Modified` handling is deferred:

```text
timeline/econ:  W/"<manifest.version>:<object.sha256>"
prices:         W/"<series.etag-or-lastModified>:<manifest.version>:<txnSha256-or-none>"
```

This allows bounded TTL staleness without silent fallback. A user may see the previous active snapshot for the TTL, but the Worker must not invent a fallback if the active manifest references a missing or corrupt object.

### Publication pipeline

The publish sequence is:

```text
1. build timemachine.db
2. run regression gates
3. export artifacts to a new version directory
4. verify local artifacts
5. bootstrap missing price-series files, if new symbols require them
6. upload snapshot objects, excluding manifest
7. read back uploaded objects and verify bytes/hash
8. upload manifest.json last
9. smoke Worker endpoints
10. record publish summary
```

Any failure before step 8 must leave the previous production manifest active. Any failure after step 8 is a post-publish incident and should be handled by publishing the previous manifest or reverting the Worker build.

## Validation Strategy

Do not switch by trusting a few sampled UI values. Use full payload parity first, then semantic UI-level checks.

This is **migration-only verification**, not a steady-state dual backend. The project should not permanently run both D1 and R2 production paths. Generate R2 artifacts, compare them against the current D1-backed payload, cut over once parity is proven, then remove the D1 serving path.

### Correctness baseline

The migration is acceptable only if each current production-data guarantee is preserved or strengthened:

| Guarantee | Current D1 path | B2 requirement |
| --- | --- | --- |
| Historical drift detection | `verify_vs_prod.py` checks row counts, `computed_daily` replacement range, and sampled historical `daily_close` values | migration cutover uses canonical D1-vs-R2 payload parity; steady-state publish uses SQLite-view row counts, schema parse, bytes, and hashes before manifest switch |
| Shortfall guard | local row counts must not be unexpectedly below prod for destructive sync scopes | manifest/export row counts must match SQLite source views before upload and before manifest switch |
| Blast radius | destructive sync is bounded by table/window policy | existing snapshot remains active until a complete new snapshot is verified |
| Publish boundary | main D1 file import has failed-execution rollback, but publication is still a mutable DB operation | manifest-last pointer switch; old snapshots remain addressable |
| Schema/view drift | generated D1 schema/views plus tests | exporter reads SQLite views; JSON parses with frontend Zod; manifest stores counts/hashes |
| Local build correctness | L1/L2 regression gates | same L1/L2 gates before export |

The important distinction: R2 does not automatically make data correct. B2 is stronger only because the publication unit becomes a validated artifact set. A minimal R2 upload without manifest, row counts, hashes, and parity gates would be a correctness regression.

### Phase 1: Migration-only parity export

Add an exporter that reads the same SQLite views used by D1:

```sql
SELECT * FROM v_daily;
SELECT * FROM v_daily_tickers;
SELECT * FROM v_fidelity_txns;
SELECT * FROM v_qianji_txns;
SELECT * FROM v_robinhood_txns;
SELECT * FROM v_empower_contributions;
SELECT * FROM v_categories;
SELECT * FROM v_market_indices;
SELECT * FROM v_holdings_detail;
```

This avoids re-implementing the API shape in Python from raw tables.

The exporter is part of the migration and the future R2 publish pipeline. The D1-vs-R2 comparison harness is temporary: keep it only until cutover confidence is established.

### Phase 2: Contract checks

Before upload:

- `timeline.json` parses with the existing frontend Zod schema.
- `econ.json` parses with the existing frontend Zod schema.
- every generated `price-txns/*.json` and every bootstrapped `prices/series/*.json` parses with the ticker sub-schemas.
- Worker-assembled `/api/prices/:symbol` fixtures parse with `TickerPriceResponseSchema`.
- row counts match SQLite source views.
- latest date matches `MAX(date)` from `computed_daily`.
- manifest hashes match local files.

### Phase 3: D1 vs R2 canonical parity

Compare the current D1-backed Worker payload to the R2-exported payload.

Canonicalization rules:

- sort object keys
- keep array order fixed by SQL `ORDER BY`
- normalize null vs absent only where the current API already treats them equivalently
- allow known volatile fields such as generated timestamps if needed
- keep numeric tolerances extremely tight: ideally exact, at most cents for money

Required comparisons:

```text
/api/timeline
/api/econ
/api/prices/:symbol for the deterministic migration symbol set
```

Deterministic migration symbol set:

- compare all price symbols when the set is <= 200 symbols; the current project is below that size.
- if the set grows above 200, compare fixed canaries (`VOO`, `SPY`, `FXAIX`, `SPAXX`), all current top-holding symbols, all symbols with non-alphanumeric path characters, and the first 100 symbols by `sha256(symbol)` sort order.

The go/no-go standard should be: zero unexpected diffs.

### Phase 4: Timemachine semantic parity

Use the timemachine as an additional user-facing semantic check, not as the only check.

Compare nodes:

- first available date
- latest date
- default 1-year start
- month ends
- quarter ends
- largest daily move dates
- major transaction dates
- latest Portfolio Positions CSV date

At each node compare:

- total
- net worth
- liabilities
- US equity / non-US equity / crypto / safe net
- allocation percentages
- top ticker values and cost basis

Also compare ranges:

- default 1-year cashflow totals
- YTD cashflow totals
- activity buys/sells/dividends totals
- cross-check matched/total

### Phase 5: UI parity

Run the same Playwright finance suite against the current D1-backed app and a temporary R2-backed preview/build:

```text
current production: D1-backed Worker
temporary preview:  R2-backed Worker
```

Key UI values should remain unchanged.

## Local Testing Plan

Local testing is a first-class requirement. The local path should exercise the same shape as production:

```text
local SQLite -> export JSON artifacts -> seed local R2 simulation -> wrangler dev Worker -> Next dev frontend
```

Avoid adding a long-lived filesystem backend to the Worker. It is acceptable for tests and scripts to read the artifact directory directly, but production Worker code should read through the R2 binding. That keeps the runtime model single-path:

```text
Frontend -> Worker -> manifest.json -> R2 object -> JSON response
```

not:

```text
Frontend -> static JSON files
```

### Layer 1: Artifact checks

Fast local checks operate directly on generated files before any Worker starts:

```text
pipeline/artifacts/r2/
  manifest.json
  snapshots/<version>/timeline.json
  snapshots/<version>/econ.json
  snapshots/<version>/price-state.json
  snapshots/<version>/price-txns/_index.json
  snapshots/<version>/price-txns/<symbolKey>.json
  prices/_index.json
  prices/series/<symbolKey>.json
```

Required checks:

- `timeline.json` and `econ.json` parse with the frontend Zod schemas.
- manifest `rowCounts` match SQLite source views.
- manifest `sha256` values match local files.
- every object referenced by the manifest exists and is non-empty.
- latest date matches the SQLite source.
- generated price transaction markers and bootstrapped price series parse and are date-keyed where applicable.

This is the fastest loop for exporter bugs.

### Layer 2: Local Worker + local R2 simulation

Use Wrangler/Miniflare's local R2 simulation. Cloudflare local development runs Worker code locally and, by default, connects bindings to local simulated resources; R2 supports both local simulation and remote bindings ([Workers local development](https://developers.cloudflare.com/workers/local-development/), [R2 Workers API](https://developers.cloudflare.com/r2/get-started/workers-api/)).

Seed local R2 with the exported artifacts. The eventual `r2_artifacts.py publish --local` command should do this; raw commands illustrate the required order, with `manifest.json` last:

```text
wrangler r2 object put portal-data/snapshots/<version>/timeline.json --file pipeline/artifacts/r2/snapshots/<version>/timeline.json --local
wrangler r2 object put portal-data/snapshots/<version>/econ.json --file pipeline/artifacts/r2/snapshots/<version>/econ.json --local
wrangler r2 object put portal-data/snapshots/<version>/price-state.json --file pipeline/artifacts/r2/snapshots/<version>/price-state.json --local
wrangler r2 object put portal-data/snapshots/<version>/price-txns/_index.json --file pipeline/artifacts/r2/snapshots/<version>/price-txns/_index.json --local
wrangler r2 object put portal-data/snapshots/<version>/price-txns/VOO.json --file pipeline/artifacts/r2/snapshots/<version>/price-txns/VOO.json --local
wrangler r2 object put portal-data/prices/_index.json --file pipeline/artifacts/r2/prices/_index.json --local
wrangler r2 object put portal-data/prices/series/VOO.json --file pipeline/artifacts/r2/prices/series/VOO.json --local
wrangler r2 object put portal-data/manifest.json --file pipeline/artifacts/r2/manifest.json --local
```

Then run the Worker locally:

```text
cd worker
npx wrangler dev --local
```

Smoke endpoints:

```text
GET http://localhost:8787/api/timeline
GET http://localhost:8787/api/econ
GET http://localhost:8787/api/prices/VOO
```

Required checks:

- response status is 200.
- response headers match expected cache/content-type behavior.
- response body hash equals the local artifact hash for timeline/econ.
- price response body equals local assembly of `prices/series/<symbolKey>.json` plus the active `price-txns/<symbolKey>.json`.
- missing manifest or missing object returns an explicit error, not stale or partial data.

### Layer 3: Local frontend against local Worker

Point the Next dev frontend at the local Worker:

```powershell
$env:NEXT_PUBLIC_TIMELINE_URL='http://localhost:8787/api'
npm run dev
```

Run the normal frontend tests and manual smoke checks against the R2 path. The browser should not know whether the data came from D1 or R2; the API shape remains unchanged.

This gives a full local rehearsal without touching production R2 or production D1.

## Cutover Model

Avoid a long-lived `DATA_BACKEND=d1 | r2` switch. That keeps two production code paths alive and defeats the simplification goal.

Cutover is a one-time migration, detailed in the `Execution Plan` below:

```text
D1-backed production -> validated R2 preview -> R2-backed production
```

The only D1 carryover is a short emergency rollback window: keep the previous D1-backed Worker deployment and untouched D1 database available for a code/config revert. Do not keep D1 as a normal runtime option after cutover.

Rollback during that short window means:

```text
previous Worker build -> D1
```

After the window expires, delete D1 sync/serving code and remove the migration-only comparison harness.

## Execution Plan

Implementation should proceed in gated phases. Do not start the next phase until the current phase has a passing local check.

### Phase 0: Branch and baseline

Deliverables:

- create the implementation branch from `main` after this design PR lands
- run or record the current green baseline for Python regression, frontend tests, and Worker tests that are relevant to this change
- capture current D1 payloads for `/api/timeline`, `/api/econ`, and the deterministic migration price symbol set

Gate:

- current production/D1 path is understood and reproducible locally or through current Worker endpoints

### Phase 1: Exporter only

Deliverables:

- artifact exporter
- manifest generation
- local artifact verifier
- unit tests for row counts, hashes, missing files, and schema parse failures

Gate:

- generated artifacts pass all local artifact checks
- exporter reads SQLite views for existing API projections
- no Worker changes are required to run this phase

Stop condition:

- if exporter output cannot match current API shape without re-implementing substantial Worker logic, stop and revisit the view/export boundary

### Phase 2: Local R2 Worker path

Deliverables:

- R2 binding in Worker config
- Worker route implementation for manifest-backed timeline/econ and split price series + transaction markers
- local R2 seeding script or documented command wrapper
- Worker tests for missing manifest/object errors and successful streaming

Gate:

- `wrangler dev --local` serves `/api/timeline`, `/api/econ`, and `/api/prices/VOO` from local R2 simulation
- response hashes for timeline/econ match local artifact hashes
- frontend can point `NEXT_PUBLIC_TIMELINE_URL` at local Worker and render normally

Stop condition:

- if local testing requires a persistent filesystem backend in Worker runtime, stop; that is architecture drift

### Phase 3: Publisher

Deliverables:

- local and remote artifact publish command
- upload-readback verification
- manifest-last behavior
- publish summary report

Gate:

- failed upload before manifest does not affect the active manifest
- missing or corrupt read-back refuses manifest publication
- local R2 simulation can rehearse the full publish sequence end to end

Stop condition:

- if a failed publish can expose a half-published timeline/econ snapshot, stop and fix publication order

### Phase 4: Migration-only parity

Deliverables:

- D1-vs-R2 canonical payload comparison
- timemachine semantic comparison report
- UI parity run against current D1-backed app and temporary R2 preview

Gate:

- zero unexpected canonical diffs for `/api/timeline` and `/api/econ`
- deterministic migration price endpoint set matches expected date-keyed output
- timemachine semantic nodes match within explicit tolerance
- UI finance suite has no R2-only failures

Stop condition:

- any unexplained data diff blocks cutover, even if the UI "looks fine"

### Phase 5: Cutover

Deliverables:

- deploy Worker with R2 as the only production serving path
- keep the previous D1-backed Worker deployment and untouched D1 database for the short emergency rollback window
- run production smoke checks immediately after deploy

Gate:

- production `/api/timeline`, `/api/econ`, and deterministic price canaries return expected hashes/counts
- frontend renders key dashboard views against production R2 path

Rollback:

- revert to the previous D1-backed Worker build while the short emergency rollback window is open
- or republish the previous known-good manifest if the Worker is healthy and the artifact is bad

### Phase 6: Cleanup

Deliverables:

- remove D1 sync code, D1 Worker SQL code, D1 schema/view generation, D1-specific tests/workflows
- remove migration-only D1/R2 comparison harness after enough successful R2 publishes
- update runbook commands and AGENTS instructions

Gate:

- at least one successful unattended R2 publish has completed after cutover
- short emergency rollback window expired without using D1
- local SQL/debug story still works through `timemachine.db`

### Definition of done

The migration is done when:

- production data is served from R2 artifacts through the thin Worker
- D1 is no longer in the steady-state production serving or publish path
- every publish is gated by regression, artifact validation, row counts, hashes, and manifest-last semantics
- local testing can rehearse exporter -> local R2 -> Worker -> frontend without touching production
- D1-specific sync/parity/schema code has been deleted or explicitly quarantined for the short emergency rollback window only
- the frontend API surface remains unchanged

## Performance Expectations

User-visible performance should be similar or slightly better.

Current cache miss:

```text
Browser -> Worker -> D1 SELECTs -> Worker assembles JSON
```

R2 timeline/econ cache miss:

```text
Browser -> Worker -> R2 object stream
```

R2 price cache miss:

```text
Browser -> Worker -> R2 price series + active transaction markers -> small JSON response
```

Frontend performance should be unchanged because JSON shape, Zod parsing, compute functions, and Recharts rendering remain the same.

Important implementation rule:

```text
timeline/econ R2 object body -> Response body
```

Avoid:

```text
timeline/econ R2 object body -> JSON.parse -> JSON.stringify -> Response
```

## Cost And Limits

Based on Cloudflare docs checked on 2026-05-02:

- R2 Standard free tier includes 10 GB-month storage, 1M Class A operations/month, and 10M Class B operations/month.
- R2 egress is free.
- The current timeline payload is only a few MB raw JSON, far below R2 object limits.
- Personal dashboard read/write volume should be far below the free tier.

R2 public `r2.dev` URLs are intended for development and can be rate-limited. If R2 is accessed directly, use a custom domain. The preferred design here avoids direct browser access and reads R2 through the Worker.

## Code Size Estimate

Approximate gross deletions:

- D1 sync/parity scripts and tests: about 1,900 LoC
- Worker D1 SQL/schema/tests/config: about 560 LoC
- real-worker D1 e2e and workflows: about 240 LoC
- D1-specific nightly/projection helpers, if removed: about 300-350 LoC

Approximate additions:

- JSON exporter
- R2 uploader
- manifest/hash verifier
- thin R2 Worker path
- contract/parity tests

Expected net reduction:

```text
about 1,800-2,500 LoC
```

If the automation changelog/email report is later simplified separately, that could remove another roughly 2,500-3,000 LoC. That is independent from the R2 migration.

## Benefits

- Data updates no longer require frontend redeploy.
- Production data becomes immutable versioned artifacts.
- Artifact rollback becomes manifest based; emergency backend rollback is a short code/config revert to the previous D1-backed Worker.
- Destructive D1 range/full sync risk disappears.
- D1 schema/view drift handling disappears.
- Worker becomes a thin API facade rather than a SQL adapter.
- Correctness gates move to offline artifact validation, which is easier to test deterministically.
- Frontend API shape can remain unchanged.

## Costs And Risks

- Need to build and maintain JSON export/upload code.
- R2 has no multi-object transaction, so manifest-last publication is mandatory.
- Need migration-only parity tests before removing D1.
- Lose convenient production SQL querying. This is acceptable if local SQLite remains the debugging/query surface and production only serves fixed dashboard read models.
- If Worker caches the manifest longer than endpoint TTLs, users may see stale data longer than intended.
- If exporter diverges from old D1 view semantics, data bugs can be introduced.
- Price cache objects are mutable and must merge by date rather than append.

Mitigations:

- read SQLite views rather than raw tables during export
- full canonical D1/R2 payload diff before cutover
- manifest hash and row-count verification
- fail publication if any referenced artifact is missing, empty, unparsable, or count-mismatched
- manifest cache no longer than endpoint TTL, immutable snapshot cache
- keep the previous D1-backed Worker deployment and untouched D1 database only during the short emergency rollback window
- date-keyed price merge with schema/hash validation

## Recommendation

Proceed with B2 if production SQL is not a requirement.

The recommended target state is:

```text
SQLite = build database + local SQL/debug surface
R2     = production serving artifact store
Worker = thin private API facade for manifest/R2/cache/auth
```

This is the only path in this note that reduces complexity while making the overall production data publication model stronger than today. The reason is not that R2 is inherently safer than D1; the reason is that B2 replaces mutable table sync with validated artifact publication.

Keep the plan narrow:

```text
Do:
  - export JSON from SQLite views
  - validate JSON with existing schemas
  - write rowCounts and sha256 hashes into manifest
  - upload snapshot objects first
  - read back and verify uploaded objects
  - update manifest last
  - use migration-only D1/R2 parity before cutover
  - delete the dual comparison harness after cutover confidence is established

Do not:
  - remove correctness gates just because artifacts are simpler
  - switch to minimal R2 without manifest-last publication
  - keep a long-lived `DATA_BACKEND=d1 | r2` production switch
  - keep Path A as the main plan unless production SQL becomes important again
```

Path A remains a fallback only. It can be made correctness-neutral by retaining a shortfall guard and adding fingerprint-driven auto-upgrade, but that moves complexity rather than removing it. It preserves production SQL, but keeps a mutable production database and a sync policy surface.

Execution starts from the gated `Execution Plan` above, not from an ad-hoc branch of experiments. The short form is:

```text
1. Keep local SQLite as the source/build/debug database.
2. Build exporter + artifact verifier first.
3. Add local R2 Worker path only after artifacts are verified.
4. Add publisher with manifest-last semantics.
5. Run migration-only D1/R2 parity and timemachine semantic checks.
6. Cut production Worker to R2 as the only serving path.
7. Keep the previous D1-backed Worker deployment and untouched D1 database for a short emergency rollback window.
8. Delete D1 sync/serving code after the rollback window expires.
```
