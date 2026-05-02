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
new:     Worker -> R2 object stream -> JSON response
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
- old snapshots are retained for rollback

Rejected variants:

- **naive full D1**: simpler sync, but loses too much drift/shortfall/blast-radius protection.
- **minimal R2**: simpler Worker path, but correctness regresses unless manifest, row counts, hashes, and parity gates are added.
- **Path A as the main plan**: keeps production SQL, but after adding fingerprints and shortfall protection it is no longer a decisive simplicity win; it still leaves a mutable production DB and a sync state machine.

## Alternative: Simplified D1 Mirror

There is a smaller fallback path that keeps D1 and avoids the R2 exporter/manifest model. It is useful only if production-side SQL remains a requirement:

```text
Raw data
-> Python ETL
-> local SQLite timemachine.db
-> validation / regression / positions verify
-> mirror local DB into D1
-> existing Worker SELECTs
-> Next static frontend
```

The key change is to stop treating D1 sync as a table-by-table semantic diff system. Instead, use a small number of mechanical rules:

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

This keeps the useful parts of the current design:

- D1 remains the production read model.
- SQL views remain the browser JSON projection layer.
- The Worker and frontend API shape stay unchanged.
- Online ad-hoc SQL queries remain possible.
- Data updates do not require redeploying the frontend.

It removes the hardest-to-reason-about parts:

- no per-table `diff` / `range` / `full` policy matrix
- no Fidelity-derived `--since` cutoff
- no `expected-drops`
- replace `verify_vs_prod.py` with a smaller row-count shortfall guard plus fingerprint gate
- no destructive-boundary policy duplicated across scripts/tests/docs
- no append-only transaction sync until there is a proven stable natural key

### Why this is not the current sync with a different name

The current model has many write modes and table-specific cutoffs:

```text
daily_close: INSERT OR IGNORE
computed_daily: range replace
computed_daily_tickers: range replace
fidelity_transactions: range replace
qianji_transactions: range replace
robinhood_transactions: range replace
computed_market_indices: full replace
computed_holdings_detail: full replace
econ_series: full replace
categories: full replace
```

The simplified model has three plain buckets:

```text
daily_close:
  normal refresh-window upsert
  auto full-prices when historical fingerprint changes
  manual override full-prices / selected-symbols

computed_daily, computed_daily_tickers:
  normal refresh-window upsert
  auto full-derived when historical fingerprint changes
  manual override full-derived

everything else:
  full replace
```

That is the mental-model reduction. The system stops asking which source tables can delete which prod rows under a per-table cutoff. Local SQLite becomes the source, and D1 becomes a mirror/read model with two cache-like exceptions: prices and large daily derived rows.

The correctness rule is:

```text
window sync is allowed only when historical rows are proven unchanged;
otherwise the sync automatically upgrades that bucket to full.
```

Manual flags remain useful for forced repair, but correctness should not depend on remembering them.

### Proposed table classification

```text
daily_close:
  refresh-window date-keyed upsert
  reason: large price cache; Yahoo can revise recent closes; splits/logic changes auto-upgrade or use manual override

computed_daily:
  refresh-window date-keyed upsert
  reason: large derived daily output; normally only recent prices/source rows change

computed_daily_tickers:
  refresh-window date-keyed upsert
  reason: largest derived table; same window as computed_daily

fidelity_transactions:
  full replace
  reason: current schema has no stable natural transaction id; legitimate duplicate rows exist

robinhood_transactions:
  full replace
  reason: current schema explicitly avoids UNIQUE because legitimate duplicate rows exist

empower_snapshots, empower_funds, empower_contributions:
  full replace
  reason: snapshots/funds use local autoincrement snapshot_id; full replace avoids id mapping bugs

qianji_transactions:
  full replace
  reason: user can edit arbitrary historical rows; no reliable recent-only correction window

computed_market_indices:
  full replace
  reason: small read model

computed_holdings_detail:
  full replace
  reason: current snapshot, no date axis

econ_series:
  full replace
  reason: small enough; FRED can revise history, so a window rule buys little

categories:
  full replace
  reason: tiny metadata

sync_meta:
  never full-replaced by table sync
  reason: holds the published fingerprints that drive mode selection
  empty/missing fingerprints must default to full for price + derived buckets
```

Do not use `append-IGNORE` for broker transactions in the first simplification. It only becomes safe after introducing and proving stable natural keys that preserve legitimate duplicate rows. Without that, append-ignore can silently drop real repeated trades.

### Write volume estimate

D1 free tier is 100k row writes/day. Rough per-day budget under this classification:

```text
refresh-window (30 days) × {daily_close 84 syms, computed_daily 1, computed_daily_tickers ~50 tickers}
  ≈ 30 × 135 ≈ ~4,000 rows

full-replace tables (every run):
  econ_series           ~8,000   ← largest always-full table
  qianji_transactions   ~5,000
  computed_market_indices ~4,000
  fidelity_transactions ~600
  empower_*             ~500
  robinhood_transactions ~200
  computed_holdings_detail ~50
  categories            ~10
  ≈ ~18,000 rows

total ≈ ~22,000 rows/day  (~22% of free tier)
```

Comfortable headroom. The single table to watch is `econ_series` — it's the largest always-full table and could double if FRED series count grows. If it crosses ~30k rows, move it to refresh-window or a coarser revision policy.

### Sync sketch

For `daily_close`, keep an incremental cache because it is the large table and because Yahoo can revise recent closes:

```sql
DELETE FROM daily_close WHERE date >= :refresh_floor;
INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES ...;
```

For `computed_daily` and `computed_daily_tickers`, use the same refresh floor in normal runs:

```sql
DELETE FROM computed_daily WHERE date >= :refresh_floor;
INSERT OR REPLACE INTO computed_daily (...) VALUES ...;

DELETE FROM computed_daily_tickers WHERE date >= :refresh_floor;
INSERT OR REPLACE INTO computed_daily_tickers (...) VALUES ...;
```

For full-replace tables:

```sql
DELETE FROM table_name;
INSERT INTO table_name (...) VALUES ...;
```

Manual override flags keep the normal path simple:

```text
--full-prices
  replace all daily_close rows from local SQLite

--symbols TSLA,NVDA
  replace daily_close only for selected symbols

--full-derived
  replace all computed_daily and computed_daily_tickers rows
```

These flags are overrides, not correctness requirements. The sync should auto-upgrade to the relevant full mode when it detects historical drift. They remain useful when the operator wants to force a repair after investigating a specific symbol or historical calculation.

### Historical fingerprints

Store small fingerprints in D1 `sync_meta` after every successful sync:

```text
price_historical_hash
price_logic_hash
derived_historical_hash
derived_logic_hash
config_hash
published_refresh_floor
```

Before each sync, compute the same fingerprints from the freshly built local SQLite DB:

```text
price_historical_hash:
  canonical hash of daily_close rows where date < refresh_floor

derived_historical_hash:
  canonical hash of computed_daily rows where date < refresh_floor
  plus canonical hash of computed_daily_tickers rows where date < refresh_floor

price_logic_hash:
  file hash of price-fetch/split-handling code

derived_logic_hash:
  file hash of allocation/replay/source-parser code

config_hash:
  hash of config that can affect historical classification/allocation
```

Default to file hashes, not explicit version constants. Version constants move the correctness burden back to the operator/code author ("remember to bump the constant"). File hashes are conservative: formatter-only or comment-only edits can trigger a full replace even when semantics did not change, but that is safe and cheap for this project. If noisy full replaces become frequent, a specific bucket can later switch to an explicit version constant with tests around the bump discipline.

Initial candidate file sets:

```text
price_logic_hash:
  pipeline/etl/prices/
  pipeline/etl/market/_yfinance.py
  pipeline/scripts/sync_prices_nightly.py

derived_logic_hash:
  pipeline/etl/allocation.py
  pipeline/etl/replay.py
  pipeline/etl/precompute.py
  pipeline/etl/categories.py
  pipeline/etl/_category_totals.py
  pipeline/etl/sources/
  pipeline/etl/qianji/
```

Hash inputs must be canonical and deterministic:

- walk directories in sorted path order
- include relative path + file bytes in the digest
- exclude caches, tests, fixtures, generated DBs, and virtualenvs
- include the config hash separately rather than relying on file hashes alone

Then choose the sync mode mechanically:

```text
if published fingerprints are missing:
  daily_close = full replace
  computed_daily + computed_daily_tickers = full replace

if price_historical_hash or price_logic_hash changed:
  daily_close = full replace
else:
  daily_close = refresh-window upsert

if derived_historical_hash or derived_logic_hash or config_hash changed:
  computed_daily + computed_daily_tickers = full replace
else:
  computed_daily + computed_daily_tickers = refresh-window upsert
```

This keeps the default path cheap without relying on operator memory. If Qianji history, category config, replay logic, source parsing, or pricing logic changes older rows, the next sync publishes the full corrected derived history automatically.

The same principle must apply to the local build stage. `build_timemachine_db.py` currently refreshes only the computed tail when the DB already exists, so historical-source/config/logic changes can leave old `computed_daily` rows stale locally. Build and sync should share one `fingerprint.py` module:

```text
build stage:
  read local build fingerprints from a local meta table or sidecar file
  if derived logic/config/source-history fingerprint changed:
    force full local recompute of computed_daily + computed_daily_tickers
  write updated local fingerprints after successful validation

sync stage:
  compare local fingerprints to D1 sync_meta fingerprints
  choose refresh-window or full sync mode for price/derived buckets
  write D1 fingerprints after successful sync
```

If implementation does not add build-layer fingerprints in the first cut, the safer fallback is to force a full local computed rebuild before every sync that would otherwise rely on derived-historical fingerprints. Do not let sync publish a locally stale incremental build.

Execution can be simple because this is a single-user dashboard and the operator accepts that the site may fail briefly during sync. Still keep these guards:

1. Build and validate the local DB before touching D1.
2. Refuse to publish if required local tables are empty.
3. Drop/recreate views only after table writes are complete, or keep views stable if table names do not change.
4. After sync, run D1 row-count checks and a `/api/timeline` smoke check.
5. Keep local SQLite as the recovery source for a re-run.
6. Keep main data writes in one `wrangler d1 execute --file` import where possible. Cloudflare documents failed remote file execution as returning the DB to its original state ([D1 getting started](https://developers.cloudflare.com/d1/get-started/)), but that is failed-import rollback, not the same operational model as an R2 manifest pointer. Schema auto-ALTERs and any separate one-off commands remain outside the main data batch.
7. Sync tables in dependency order (sources before derived) if a future implementation splits the batch. If a derived-table write succeeds after a source-table write fails, the cross-table state is torn; the post-sync smoke check must catch this, and recovery is a re-run from local SQLite.

### Trade-off

This option is less clean than R2's immutable artifact model, but it is much less disruptive:

- fewer moving parts than R2
- less code churn
- preserves SQL extensibility
- preserves the current Worker/frontend contract
- removes most current sync-policy complexity

Its main downsides:

- D1 remains a mutable production database.
- D1 remote file imports have failed-execution rollback, but there is no explicit active-snapshot pointer; rollback and time travel are operator workflows rather than a one-key manifest flip.
- Schema auto-ALTERs and any split commands can still happen outside the main data import batch.
- Historical fingerprinting adds some implementation and test complexity.
- File-based logic hashes can trigger spurious full replaces on non-semantic edits. These are correct but wasteful; if they become frequent, switch the affected hash to an explicit version constant for that module.
- `backup_d1.py` is retained and arguably becomes more critical under this path — full-replace destroys the previous sync's state inside D1 itself, so the backup workflow is the only off-D1 history.

For a single-user dashboard where sync runs unattended and access during the sync window is not important, that risk may be acceptable.

### Code-size estimate

Expected net reduction is roughly:

```text
400-700 LoC
```

The likely deletions/reductions are:

- shrink or replace `verify_vs_prod.py` with a smaller shortfall/fingerprint gate
- delete or shrink `sync_policy.py`
- simplify `sync_to_d1.py`
- shrink or delete most `test_sync_diff.py`
- remove `expected-drops` handling from automation

This is less LoC reduction than R2, but it attacks the highest mental-model cost with a smaller migration. The number is lower than the two-mode full-mirror idea because this keeps refresh-window handling for large derived tables and adds fingerprint-driven auto-upgrade plus shortfall-guard logic.

## R2 Object Layout

Two trees with different semantics: a versioned, immutable snapshot tree for the ETL-derived bundle (timeline + econ), and an unversioned mutable date-keyed cache tree for prices.

```text
r2://portal-data/
  manifest.json                              # pointer for timeline + econ
  snapshots/2026-05-02T120000Z/
    timeline.json
    econ.json
  prices/                                    # endpoint artifacts, NOT under manifest
    _index.json                              # {symbol: maxDate}
    VOO.json                                 # {symbol, prices, transactions}
    SPY.json
    FXAIX.json
```

`manifest.json` points to the active snapshot and records hashes/counts:

```json
{
  "version": "2026-05-02T120000Z",
  "generatedAt": "2026-05-02T12:00:00Z",
  "latestDate": "2026-05-01",
  "objects": {
    "timeline": {
      "key": "snapshots/2026-05-02T120000Z/timeline.json",
      "sha256": "...",
      "bytes": 4600000
    }
  },
  "rowCounts": {
    "daily": 1234,
    "dailyTickers": 45678,
    "fidelityTxns": 123
  }
}
```

Publication order matters because R2 does not provide a multi-object transaction:

1. Export JSON locally.
2. Validate JSON locally.
3. Upload immutable snapshot objects.
4. Read back key objects and verify hashes/sizes.
5. Update `manifest.json` last.

The manifest acts as the atomic publish pointer. Users should never read a half-published snapshot if the Worker always resolves data through the manifest.

## Prices: Stateless Incremental (Outside The Manifest)

Prices live outside the versioned snapshot tree. The manifest does not reference `prices/`. This is a deliberate split — prices and the timeline bundle have different consistency stories:

- `timeline.json` / `econ.json` — derived bundles, produced by one ETL run, must be point-in-time consistent (the manifest enforces this).
- `prices/<symbol>.json` — mutable full endpoint payload for `GET /prices/:symbol`, produced by ETL export and updated by the separate nightly price cron (`prices-sync.yml` / `sync_prices_nightly.py`).

Forcing prices into the versioned snapshot tree would break this for no benefit: every snapshot would have to copy ~84 unchanged price files, and the prices cron would have to publish a new manifest, coordinating with timeline cron.

Each symbol file stores the complete endpoint response, not just the daily-close rows:

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

The ETL exporter rewrites the full symbol payload when local transaction history changes. The nightly price job updates only the `prices` array by date-keyed merge and preserves `transactions` unless the ETL exporter has published a newer file. That keeps the Worker thin: `/prices/:symbol` still streams one R2 object.

### yfinance call count must not change

The current nightly script is the constraint to preserve:

```text
SELECT symbol, MAX(date) FROM daily_close GROUP BY symbol   # state read
→ compute per-symbol gap (today - max_date)
→ yfinance fetch only the gap
→ INSERT OR IGNORE
```

After migration the algorithm is identical, only the state read changes:

```text
GET prices/_index.json                                       # state read
→ compute per-symbol gap
→ yfinance fetch only the gap
→ for each symbol with new rows: GET prices/SYMBOL.json, merge payload.prices by date, PUT back
→ PUT prices/_index.json
```

yfinance call count is **identical to today** — gap-only, per-symbol. R2 traffic per nightly run is roughly: 1 GET on `_index.json`, K GETs + K PUTs on changed symbols (K ≈ full set on weekdays, 0 on weekends), 1 PUT on `_index.json`. Each symbol file update must be an idempotent date-keyed merge of the `prices` array, not blind append, because recent Yahoo closes and split-adjusted history can be revised. Free tier (1M Class A / 10M Class B per month) is not in danger.

### Closed-position grace coupling

`sync_prices_nightly.py` currently reads `fidelity_transactions` from D1 to reconstruct `{symbol: (firstBuy, lastSell)}` and stop fetching ~7 days after a position is fully closed. After D1 is removed, that state has to come from somewhere.

Preferred: have the ETL exporter emit a small `prices_state.json` artifact alongside the timeline snapshot:

```json
{
  "VOO":  { "firstBuy": "2022-03-14", "lastSell": null,         "maxDate": "2026-05-01" },
  "TSLA": { "firstBuy": "2021-09-02", "lastSell": "2024-11-08", "maxDate": "2024-11-15" }
}
```

The prices cron then needs only one fetch (`prices_state.json`) instead of pulling and parsing the full 4.6 MB `timeline.json` to recompute holding periods. This makes "what state does the prices cron depend on" an explicit, narrow contract — useful when adding Robinhood prices later under the same flow.

`prices_state.json` can live under `prices/` (it's prices-cron state, not ETL output consumed by the browser) and is overwritten in place each ETL run.

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
request -> route -> manifest lookup -> R2 get -> stream Response
```

It should not parse and re-stringify JSON on the hot path, reshape payloads, run business logic, run SQL, or perform runtime Zod validation. The data contract is enforced before publication, not inside the request path.

## Implementation Design

The implementation should be split into small components with one clear responsibility each. The steady-state production path should have no D1 dependency.

### Data correctness invariants

These are non-negotiable. Implementation is allowed to be simple, but it must preserve these invariants:

1. **Single source of truth:** every production artifact is exported from the same local `timemachine.db` that passed regression gates.
2. **Shape compatibility:** timeline/econ artifacts are produced from the same SQLite view projections used by the current D1 Worker where views exist; raw-table export is allowed only for data that has no current view projection, such as the future price cache.
3. **Manifest-last publication:** `manifest.json` is the only active snapshot pointer for timeline/econ. It is written only after every referenced snapshot object has been uploaded and verified.
4. **No partial active snapshot:** the Worker must resolve timeline/econ objects only through the active manifest. It must never list a snapshot directory and infer "latest".
5. **Write-once snapshots:** objects under `snapshots/<version>/` are immutable by convention. A failed publish creates a new version on retry instead of overwriting a previous version.
6. **Row-count guard:** manifest row counts must match SQLite source-view row counts before upload and after upload verification.
7. **Hash guard:** manifest `sha256` and `bytes` must match local files and R2 read-back bytes before the manifest is published.
8. **Schema guard:** exported JSON must parse with the frontend schemas before upload. Schema validation is a publish-time gate, not a Worker hot-path cost.
9. **No silent stale data:** if manifest or a referenced object is missing, the Worker returns an explicit 5xx error. It must not fall back to an older object unless rollback was explicitly requested by changing the manifest/config.
10. **Price cache isolation:** `prices/` objects are mutable date-keyed caches outside the timeline/econ manifest. A bad price-cache update must not change the active timeline/econ snapshot.

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
syncMeta              = publish metadata, or null if intentionally omitted
errors                = {}
```

Important difference from the current runtime Worker: the exporter should fail closed. The D1 Worker currently fail-opens optional sections because a live production query can fail independently. During offline export, any query failure is a build/publish failure. Do not encode exporter failures as `errors` in the published artifact.

Minimum timeline gates:

- `daily` must be non-empty.
- `categories` must be non-empty.
- all expected view queries must succeed.
- output must parse with `TimelineDataSchema`.
- `syncMeta` is informational; parity checks may normalize timestamp-like fields, but financial data sections must match exactly.

`econ.json` is assembled as:

```text
generatedAt = sync/publish timestamp
snapshot    = object from SELECT key, value FROM v_econ_snapshot
series      = object from SELECT key, points FROM v_econ_series_grouped
```

Keep `series[key]` as the SQLite JSON string produced by `json_group_array`, matching the current API. The frontend `EconDataSchema` already accepts and parses that string.

`prices/<symbol>.json` is assembled as the full current price endpoint response:

```text
symbol       = uppercase request symbol
prices       = SELECT date, close
               FROM daily_close
               WHERE symbol = :symbol
               ORDER BY date
transactions = SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount
               FROM fidelity_transactions
               WHERE symbol = :symbol
               ORDER BY id
```

The exporter should generate a file for every symbol the frontend can request, initially every distinct `daily_close.symbol` plus any representative/top-holding symbols needed by tests. The nightly price updater then preserves the endpoint shape and updates only `prices`.

### Artifact contract

Use a local artifact directory as the staging area:

```text
pipeline/artifacts/r2/<version>/
  manifest.json
  snapshots/<version>/timeline.json
  snapshots/<version>/econ.json
  prices/_index.json
  prices/<symbol>.json
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
  prices/_index.json
  prices/<symbol>.json
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
    "holdingsDetail": 50
  }
}
```

Do not include large hashes that require the Worker to re-read and hash object bodies on every request. The Worker can trust a published manifest because the publisher already verified it; request-time verification should be existence/content-type/streaming only.

### Component boundaries

Suggested implementation components:

```text
pipeline/scripts/export_r2_artifacts.py
  input:  timemachine.db
  output: pipeline/artifacts/r2/<version>/
  does:   read SQLite views, write JSON files, write manifest, write export summary

pipeline/scripts/verify_r2_artifacts.py
  input:  artifact directory
  does:   Zod/schema check, row-count check, sha256/bytes check, latest-date check

pipeline/scripts/publish_r2_artifacts.py
  input:  verified artifact directory
  does:   upload snapshot objects, read back and verify, upload manifest last
  modes:  --local for Miniflare/local R2, default remote for production

pipeline/scripts/compare_d1_r2_payloads.py
  input:  current D1 Worker URL, local/preview R2 Worker URL
  does:   migration-only canonical parity diff
  lifetime: delete after cutover confidence is established

worker/src/index.ts
  does:   route, manifest lookup, R2 get, stream response, cache/error headers
  does not: SQL, JSON reshape, business compute, runtime Zod
```

The exact filenames can change during implementation, but the ownership boundaries should not. Export, verification, publication, migration parity, and runtime serving should stay separate.

### Worker behavior

Routes should preserve the current public API:

```text
GET /api/timeline      -> manifest.objects.timeline.key
GET /api/econ          -> manifest.objects.econ.key
GET /api/prices/:sym   -> prices/<sym>.json
```

Required behavior:

- Strip the optional `/api` prefix exactly as today.
- Fetch `manifest.json` for timeline/econ; cache it briefly.
- Stream R2 object bodies directly when possible.
- Preserve current cache TTL intent: timeline around 60s, econ around 600s, prices around 300s unless implementation finds a better existing constant.
- Return explicit errors for missing manifest, missing referenced object, or malformed route.
- Do not parse `timeline.json` or `econ.json` on the hot path.

### Publication pipeline

The publish sequence is:

```text
1. build timemachine.db
2. run regression gates
3. export artifacts to a new version directory
4. verify local artifacts
5. upload snapshot objects, excluding manifest
6. read back uploaded objects and verify bytes/hash
7. upload manifest.json last
8. smoke Worker endpoints
9. record publish summary
```

Any failure before step 7 must leave the previous production manifest active. Any failure after step 7 is a post-publish incident and should be handled by publishing the previous manifest or reverting the Worker build.

## Validation Strategy

Do not switch by trusting a few sampled UI values. Use full payload parity first, then semantic UI-level checks.

This is **migration-only verification**, not a steady-state dual backend. The project should not permanently run both D1 and R2 production paths. Generate R2 artifacts, compare them against the current D1-backed payload, cut over once parity is proven, then remove the D1 serving path.

### Correctness baseline

The migration is acceptable only if each current production-data guarantee is preserved or strengthened:

| Guarantee | Current D1 path | B2 requirement |
| --- | --- | --- |
| Historical drift detection | `verify_vs_prod.py` checks row counts, `computed_daily` replacement range, and sampled historical `daily_close` values | canonical payload/hash parity, with zero unexpected diffs before cutover |
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
- selected `prices/*.json` parse with the ticker schema.
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
/api/prices/VOO
/api/prices/SPY
/api/prices/FXAIX
/api/prices/SPAXX
/api/prices/<top holdings>
```

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
  prices/_index.json
  prices/<symbol>.json
```

Required checks:

- `timeline.json` and `econ.json` parse with the frontend Zod schemas.
- manifest `rowCounts` match SQLite source views.
- manifest `sha256` values match local files.
- every object referenced by the manifest exists and is non-empty.
- latest date matches the SQLite source.
- representative `prices/*.json` files parse and are date-keyed.

This is the fastest loop for exporter bugs.

### Layer 2: Local Worker + local R2 simulation

Use Wrangler/Miniflare's local R2 simulation. Cloudflare local development runs Worker code locally and, by default, connects bindings to local simulated resources; R2 supports both local simulation and remote bindings ([Workers local development](https://developers.cloudflare.com/workers/local-development/), [R2 Workers API](https://developers.cloudflare.com/r2/get-started/workers-api/)).

Seed local R2 with the exported artifacts:

```text
wrangler r2 object put portal-data/manifest.json --file pipeline/artifacts/r2/manifest.json --local
wrangler r2 object put portal-data/snapshots/<version>/timeline.json --file pipeline/artifacts/r2/snapshots/<version>/timeline.json --local
wrangler r2 object put portal-data/snapshots/<version>/econ.json --file pipeline/artifacts/r2/snapshots/<version>/econ.json --local
wrangler r2 object put portal-data/prices/_index.json --file pipeline/artifacts/r2/prices/_index.json --local
wrangler r2 object put portal-data/prices/VOO.json --file pipeline/artifacts/r2/prices/VOO.json --local
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
- missing manifest or missing object returns an explicit error, not stale or partial data.

### Layer 3: Local frontend against local Worker

Point the Next dev frontend at the local Worker:

```powershell
$env:NEXT_PUBLIC_TIMELINE_URL='http://localhost:8787/api'
npm run dev
```

Run the normal frontend tests and manual smoke checks against the R2 path. The browser should not know whether the data came from D1 or R2; the API shape remains unchanged.

This gives a full local rehearsal without touching production R2 or production D1.

## Cutover Plan

Avoid a long-lived `DATA_BACKEND=d1 | r2` switch. That keeps two production code paths alive and defeats the simplification goal.

Suggested rollout:

1. Implement R2 exporter/uploader.
2. Implement the R2 Worker path in a short-lived branch or preview deployment.
3. Run D1-vs-R2 canonical parity locally and against the preview.
4. Run timemachine semantic parity and UI parity against the preview.
5. Deploy the R2 Worker as the only production serving path.
6. Keep the old D1 database untouched for a short rollback window, but do not keep D1 serving code as a normal runtime option.
7. Remove D1 sync/Worker code after the first successful production R2 publish plus rollback-window expiry.

Rollback during the short window is a code/config revert to the previous Worker build, not a permanent dual-backend mode:

```text
previous Worker build -> D1
```

## Execution Plan

Implementation should proceed in gated phases. Do not start the next phase until the current phase has a passing local check.

### Phase 0: Branch and baseline

Deliverables:

- create the implementation branch from `main` after this design PR lands
- run or record the current green baseline for Python regression, frontend tests, and Worker tests that are relevant to this change
- capture current D1 payloads for `/api/timeline`, `/api/econ`, and representative `/api/prices/:symbol`

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
- Worker route implementation for manifest-backed timeline/econ and date-keyed prices
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
- representative price endpoints match expected date-keyed output
- timemachine semantic nodes match within explicit tolerance
- UI finance suite has no R2-only failures

Stop condition:

- any unexplained data diff blocks cutover, even if the UI "looks fine"

### Phase 5: Cutover

Deliverables:

- deploy Worker with R2 as the only production serving path
- keep old D1 database untouched for the rollback window
- run production smoke checks immediately after deploy

Gate:

- production `/api/timeline`, `/api/econ`, and representative `/api/prices/:symbol` return expected hashes/counts
- frontend renders key dashboard views against production R2 path

Rollback:

- revert to the previous Worker build while the rollback window is open
- or republish the previous known-good manifest if the Worker is healthy and the artifact is bad

### Phase 6: Cleanup

Deliverables:

- remove D1 sync code, D1 Worker SQL code, D1 schema/view generation, D1-specific tests/workflows
- remove migration-only D1/R2 comparison harness after enough successful R2 publishes
- update runbook commands and AGENTS instructions

Gate:

- at least one successful unattended R2 publish has completed after cutover
- rollback window expired without using D1
- local SQL/debug story still works through `timemachine.db`

### Definition of done

The migration is done when:

- production data is served from R2 artifacts through the thin Worker
- D1 is no longer in the steady-state production serving or publish path
- every publish is gated by regression, artifact validation, row counts, hashes, and manifest-last semantics
- local testing can rehearse exporter -> local R2 -> Worker -> frontend without touching production
- D1-specific sync/parity/schema code has been deleted or explicitly quarantined for temporary rollback only
- the frontend API surface remains unchanged

## Performance Expectations

User-visible performance should be similar or slightly better.

Current cache miss:

```text
Browser -> Worker -> D1 SELECTs -> Worker assembles JSON
```

R2 cache miss:

```text
Browser -> Worker -> R2 object stream
```

Frontend performance should be unchanged because JSON shape, Zod parsing, compute functions, and Recharts rendering remain the same.

Important implementation rule:

```text
R2 object body -> Response body
```

Avoid:

```text
R2 object body -> JSON.parse -> JSON.stringify -> Response
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
- Rollback becomes manifest/config based.
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
- If Worker streams stale manifest due to caching mistakes, users may see old data.
- If exporter diverges from old D1 view semantics, data bugs can be introduced.
- Price cache objects are mutable and must merge by date rather than append.

Mitigations:

- read SQLite views rather than raw tables during export
- full canonical D1/R2 payload diff before cutover
- manifest hash and row-count verification
- fail publication if any referenced artifact is missing, empty, unparsable, or count-mismatched
- short manifest cache, immutable snapshot cache
- keep the old D1 database untouched during a short rollback window, without keeping a long-lived dual backend
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
7. Keep old D1 data untouched for a short rollback window.
8. Delete D1 sync/serving code after the rollback window expires.
```
