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

The main dashboard endpoints stay the same. Ticker chart data moves from one request per symbol to one lazy bundle request:

```text
GET /api/timeline
GET /api/econ
GET /api/prices
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
- the Worker keeps a narrow API but reads through a manifest pointer
- row counts, hashes, schema parsing, and baseline-vs-R2 parity are hard gates before cutover
- `manifest.json` is updated last, after every referenced object is uploaded and verified
- old R2 snapshots are retained for manifest rollback

The design principle is **small, boring, correct**. This is a personal dashboard, not an enterprise data platform, but that does not lower the correctness bar. Prefer copying a few extra JSON objects over introducing mutable state, split ownership, or a second runtime data model.

Rejected variants:

- **naive full D1**: simpler sync, but loses too much drift/shortfall/blast-radius protection.
- **minimal R2**: simpler Worker path, but correctness regresses unless manifest, row counts, hashes, and parity gates are added.
- **Path A as the main plan**: keeps production SQL, but after adding fingerprints and shortfall protection it is no longer a decisive simplicity win; it still leaves a mutable production DB and a sync state machine.

## Alternative: Simplified D1 Mirror

Path A was considered and rejected as the main plan. It preserves production SQL, but correctness-neutral simplification still needs shortfall guards, fingerprints, sync metadata, and a mutable production database. Since local SQLite is sufficient for SQL/debugging, it moves complexity more than it removes it.

## R2 Object Layout

Use one complete, versioned snapshot tree. The active manifest points at a complete API-shaped artifact set:

```text
r2://portal-data/
  manifest.json                              # active snapshot pointer
  snapshots/2026-05-02T120000Z/
    timeline.json
    econ.json
    prices.json                              # full /prices bundle, keyed by symbol
```

Publication order matters because R2 does not provide a multi-object transaction:

1. Export JSON locally.
2. Validate JSON locally.
3. Create a fresh version id; never reuse a version directory.
4. Refuse to upload if any `snapshots/<version>/...` key already exists.
5. Upload immutable snapshot objects.
6. Read back objects and verify hashes/sizes.
7. Update `manifest.json` last.

The manifest acts as the atomic publish pointer. Users should never read a half-published snapshot if the Worker always resolves data through the manifest.

Keep old snapshots unless storage becomes a problem. At today's scale, 5-20 MB per publish is cheap enough that retention should start as "do not actively delete"; add a simple N-day GC only after the R2 bucket has real growth data.

## Prices: Versioned Bundle Artifact

`prices.json` stores every current ticker chart payload keyed by symbol:

```json
{
  "VOO": {
    "symbol": "VOO",
    "prices": [
      { "date": "2026-05-01", "close": 512.34 }
    ],
    "transactions": [
      { "runDate": "2025-01-15", "actionType": "buy", "quantity": 1.23, "price": 480.0, "amount": 590.4 }
    ]
  }
}
```

This intentionally republishes the full price bundle between versions. That is simpler and safer than a mutable `prices/` tree with separate transaction-marker artifacts, index files, bootstrap rules, cross-writer ownership, and hundreds of per-object publish calls.

The frontend fetches `/api/prices` lazily the first time a ticker chart is opened, caches the bundle in memory, and then looks up `bundle[symbol]`. The Worker streams `prices.json` directly and does not parse the bundle on the hot path. Do not put prices into `timeline.json`; that would make every dashboard load pay the price-chart cost even when no ticker chart is opened.

For v1, use canonical ticker strings as object keys inside `prices.json`. The exporter must fail loudly if a symbol contains `/` or another path-unsafe character. Add encoding later only when a real ticker requires it.

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
GET active snapshots/<version>/prices.json                   # current maxDate by symbol
→ compute per-symbol gap
→ yfinance fetch only the gap
→ merge each changed symbol by date
→ write a fresh complete snapshot version
→ update manifest last
```

yfinance call count is **identical to today** — gap-only, per-symbol. A full versioned price snapshot does **not** mean a full Yahoo refetch. Price publishers must carry forward existing price rows from the active snapshot and fetch only the missing/revision window. Re-fetching full price history from Yahoo on every publish is a correctness/performance bug.

R2 traffic increases because the whole price bundle is copied into the new snapshot, but this is acceptable for a small personal dashboard and removes a whole class of mutable-cache correctness questions. Each symbol update must be an idempotent date-keyed merge of that symbol's `prices` array, not blind append, because recent Yahoo closes and split-adjusted history can be revised.

The nightly price job should use the same publisher path as the ETL run. It may carry forward unchanged `timeline.json` and `econ.json` from the active snapshot, update `prices.json`, verify the complete artifact set, then switch the manifest. Do not keep a separate mutable price publisher.

## Why Keep The Worker

Keep the Worker. Do not expose the R2 bucket or object layout directly to the browser.

The reason is not merely same-origin access. The Worker keeps the production contract narrow:

- R2 remains private; personal finance JSON is not directly public bucket content.
- Cloudflare Access/auth stays at the API boundary.
- The frontend uses `/api/timeline`, `/api/econ`, and lazy `/api/prices`.
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
2. **Shape compatibility:** artifacts are endpoint-shaped JSON produced from the same SQLite view projections and source queries as the current D1 Worker.
3. **Complete versioned snapshot:** `manifest.json` points to one complete snapshot containing `timeline.json`, `econ.json`, and `prices.json`. The Worker must never list a snapshot directory and infer "latest".
4. **Write-once snapshots:** a publish must create a fresh version id and refuse to overwrite any existing `snapshots/<version>/...` key, using a HEAD check or conditional put such as `If-None-Match: *`.
5. **Publish-time gates:** before the manifest switch, every referenced object must exist, be non-empty, match local `bytes`/`sha256`, match SQLite row counts, and parse with the frontend schemas.
6. **Manifest-last publication:** `manifest.json` is updated only after all snapshot objects are uploaded and read-back verified. Missing manifests or referenced objects return explicit 5xx errors; the Worker must not silently fall back to an older object.
7. **No full Yahoo refetch:** versioning price artifacts means copying/merging local JSON, not re-fetching full history. Price publishers must carry forward active price rows and fetch only the missing/revision window from Yahoo.
8. **Single publisher:** at most one publisher may be in flight at a time. Enforce this with a local file lock or one chained automation entry. Two concurrent publishers derived from the same active manifest can silently drop one side's diff when the later manifest wins.

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

`prices.json` is assembled as a symbol-keyed bundle of the current price chart responses:

```text
snapshots/<version>/prices.json
  {
    [symbol]: {
      symbol       = canonical symbol
      prices       = SELECT date, close
                     FROM daily_close
                     WHERE symbol = :symbol
                     ORDER BY date
      transactions = SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount
                     FROM fidelity_transactions
                     WHERE symbol = :symbol
                     ORDER BY id
    }
  }
```

The exporter should generate one bundle entry for every symbol the frontend can request: every distinct `daily_close.symbol`, plus any representative group ticker that needs an on-demand chart. Missing transaction rows become `transactions: []`.

The nightly price publisher uses the same publish path with a price-update invocation: carry forward unchanged `timeline.json` and `econ.json`; merge gap-fetched price rows into `prices.json`; verify the complete new version; then update the manifest last. This preserves the same user-facing atomicity as an ETL publish.

Carry-forward can re-upload unchanged objects in v1; the data volume is acceptable. Revisit only if publish time becomes a real problem.

Per-symbol transactions in `prices.json` duplicate filtered slices of `timeline.json.fidelityTxns`. This denormalization is intentional: ticker charts stay self-contained on the frontend and the Worker does not need joins or large JSON parsing.

### Artifact contract

Use a local artifact directory that mirrors the production R2 key layout:

```text
pipeline/artifacts/r2/
  manifest.json
  snapshots/<version>/timeline.json
  snapshots/<version>/econ.json
  snapshots/<version>/prices.json
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
  snapshots/<version>/prices.json
```

`manifest.json` should be explicit enough to serve as the publish receipt:

```json
{
  "version": "2026-05-02T170000Z",
  "generatedAt": "2026-05-02T17:00:00Z",
  "source": {
    "gitCommit": "abc1234",
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
    "prices": {
      "key": "snapshots/2026-05-02T170000Z/prices.json",
      "sha256": "...",
      "bytes": 2600000,
      "contentType": "application/json"
    }
  }
}
```

`objects` is the fixed endpoint artifact set (`timeline`, `econ`, `prices`). Per-symbol keys live inside `prices.json`, not in the manifest, so daily publication stays to three snapshot object uploads plus the manifest flip.

Keep row counts in `reports/export-summary.json`, not in the manifest. They are a publisher/verifier concern; the Worker and frontend do not read them.

Do not include hashes that require the Worker to re-read and hash object bodies on every request. The Worker can trust a published manifest because the publisher already verified it; request-time verification should be existence/content-type/streaming only.

Keys in `prices.json` are canonical ticker strings. The exporter must reject path-unsafe symbols in v1 rather than silently generating ambiguous object keys. Path-unsafe means containing `/`, `\`, `..`, NUL, or control characters; alphanumerics plus `.`, `-`, `_`, `=`, and `^` are accepted (`CNY=X`, `^GSPC`, `000300.SS`).

### Component boundaries

Suggested implementation components:

```text
pipeline/scripts/r2_artifacts.py
  subcommands:
    export   -- read SQLite views, write JSON files, write manifest, write export summary
    verify   -- row-count check, sha256/bytes check, latest-date check, schema check, optional baseline diff
    publish  -- upload objects, read back and verify, upload manifest last
    capture-baseline -- capture current D1 API payloads for migration-only parity
  modes:
    --local  -- publish to Miniflare/local R2
    --remote -- publish to production R2

scripts/validate_r2_artifacts_zod.ts
  input:  artifact directory
  does:   run the existing frontend Zod schemas against generated JSON

pipeline/migration-baseline/
  temporary directory of current D1 API payloads captured after verify_vs_prod.py passes
  compared by r2_artifacts.py verify --baseline, then deleted after cutover confidence

worker/src/index.ts
  does:   route, manifest lookup, R2 get, stream endpoint artifacts, cache/error headers
  does not: SQL, JSON reshape, business compute, runtime Zod

pipeline/scripts/run_automation.py
  change: replace the current verify_vs_prod.py + sync_to_d1.py publish step
          with r2_artifacts.py export -> verify -> publish --remote after migration parity passes
```

Prefer one Python CLI over several near-identical scripts. The ownership boundaries still matter: export, verification, publication, migration parity, and runtime serving should stay separate even if the first three are subcommands in one file.

### Worker behavior

Routes should keep the dashboard API small and stream-oriented:

```text
GET /api/timeline      -> manifest.objects.timeline.key
GET /api/econ          -> manifest.objects.econ.key
GET /api/prices        -> manifest.objects.prices.key
```

During the transition, the D1-backed Worker can keep `/api/prices/:symbol` as a compatibility route. The R2 steady-state path should not need it; the frontend fetches `/api/prices` once, validates it with `TickerPricesBundleSchema`, and picks symbols client-side.

Required behavior:

- Strip the optional `/api` prefix exactly as today.
- Fetch `manifest.json` for timeline/econ/prices; cache it briefly.
- Stream endpoint artifact object bodies directly.
- For `/prices`, stream the manifest-listed `prices.json` object. Do not parse the bundle in the Worker just to pick one ticker.
- If a manifest-referenced object is missing, return an explicit error.
- If an R2 read fails transiently, return an explicit 5xx. A single same-request retry is acceptable, but do not fall back to a previous manifest or older object.
- Preserve current cache TTL intent: timeline around 60s, econ around 600s, prices around 300s unless implementation finds a better existing constant.
- Return explicit errors for missing manifest, missing referenced object, or malformed route.
- Do not parse endpoint JSON on the hot path.

### Cache strategy

Cache `manifest.json` only briefly in the Worker, for example with a 30s in-memory TTL. Preserve today's endpoint TTL intent (`timeline` ~60s, `econ` ~600s, `prices` ~300s). Defer ETag/304 support to v2.

### Publication pipeline

The publish sequence is:

```text
1. build timemachine.db
2. run regression gates
3. export artifacts to a new version directory
4. verify local artifacts
5. check that no `snapshots/<version>/...` key already exists
6. upload snapshot objects, excluding manifest
7. read back uploaded objects and verify bytes/hash
8. upload manifest.json last
9. record publish summary
```

Any failure before step 8 must leave the previous production manifest active. Any failure after step 8 is a post-publish incident and should be handled by publishing the previous manifest or reverting the Worker build.

Post-publish health check is separate and non-blocking in v1: `GET /api/timeline` should return 200 with a non-empty body; on failure log and alert, but do not auto-rollback.

Remote publishing can start with `wrangler r2 object put` because it matches the existing Cloudflare CLI workflow. The remote path must hold the single-publisher lock, refuse to overwrite `snapshots/<version>/...` keys, upload all snapshot objects first, read back and verify `bytes`/`sha256`, then upload and verify `manifest.json` last. Local publishing should avoid one Wrangler process per object; use a bulk local-R2 seed path and verify the final `manifest.json` through Wrangler or the Worker. Re-uploading unchanged carry-forward objects is acceptable at this scale; revisit only if publish time becomes a real problem.

Hard constraint: steady-state publish must not perform one R2 operation per ticker. The v1 artifact set is exactly three snapshot objects (`timeline.json`, `econ.json`, `prices.json`) plus `manifest.json`; the remote publisher may check/read back those objects, but network operations must stay proportional to endpoint artifacts, not symbol count.

## Validation Strategy

Do not switch by trusting a few sampled UI values. Use full API payload parity first. If D1 and R2 return the same canonical JSON, the pure frontend compute layer will produce the same timemachine values and UI data.

This is **migration-only verification**, not a steady-state dual backend. Capture current D1-backed API payloads once, compare R2 artifacts against that baseline, cut over once parity is proven, then remove the D1 serving path.

### Correctness baseline

The migration is acceptable only if each current production-data guarantee is preserved or strengthened:

| Guarantee | Current D1 path | B2 requirement |
| --- | --- | --- |
| Historical drift detection | `verify_vs_prod.py` checks row counts, `computed_daily` replacement range, and sampled historical `daily_close` values | migration cutover uses canonical baseline-vs-R2 payload parity; steady-state publish uses SQLite-view row counts, schema parse, bytes, and hashes before manifest switch |
| Shortfall guard | local row counts must not be unexpectedly below prod for destructive sync scopes | export-summary row counts must match SQLite source views before upload and before manifest switch |
| Blast radius | destructive sync is bounded by table/window policy | existing snapshot remains active until a complete new snapshot is verified |
| Publish boundary | main D1 file import has failed-execution rollback, but publication is still a mutable DB operation | manifest-last pointer switch; old snapshots remain addressable |
| Schema/view drift | generated D1 schema/views plus tests | exporter reads SQLite views; JSON parses with frontend Zod; export summary stores counts and manifest stores object hashes |
| Local build correctness | L1/L2 regression gates | same L1/L2 gates before export |

The important distinction: R2 does not automatically make data correct. B2 is stronger only because the publication unit becomes a validated artifact set. A minimal R2 upload without manifest, row counts, hashes, and parity gates would be a correctness regression.

### Migration-only parity export

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
SELECT key, points FROM v_econ_series_grouped;
SELECT key, value FROM v_econ_snapshot;
```

This avoids re-implementing the API shape in Python from raw tables.

The exporter is part of the migration and the future R2 publish pipeline. The D1 baseline directory is temporary: keep it only until cutover confidence is established.

### Contract checks

Before upload:

- `timeline.json` parses with the existing frontend Zod schema.
- `econ.json` parses with the existing frontend Zod schema.
- `prices.json` parses with `TickerPricesBundleSchema`, and every entry parses with `TickerPriceResponseSchema`.
- `reports/export-summary.json` row counts match SQLite source views.
- latest date matches `MAX(date)` from `computed_daily`.
- manifest hashes match local files.

### Baseline vs R2 canonical parity

Compare the captured D1-backed API payloads to the R2-exported payloads.

This is migration-only validation, not a permanent parallel check. During the migration-only parity phase, while D1 is still live, run the existing `verify_vs_prod.py` first so current D1 is known-good against local SQLite. Then capture `/api/timeline`, `/api/econ`, and every `/api/prices/:symbol` payload into `pipeline/migration-baseline/`. `r2_artifacts.py verify --baseline pipeline/migration-baseline` checks whether the new exporter reproduces the existing API contract. Delete the baseline after cutover confidence is established.

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

- compare all price symbols for v1. The current project is small enough that sampling is unnecessary.
- if the set ever becomes too large, define a deterministic subset then; do not hand-pick a few symbols.

The go/no-go standard should be: zero unexpected diffs.

### Frontend sanity check

Before flipping production, open the local-R2-backed dev frontend and visually confirm the dashboard renders. This is a sanity check, not a second parity test.

After cutover, the `e2e-real-worker` workflow should stop being a D1-view drift check. Either convert it to hit the production R2-backed endpoint, or replace it with a local-R2 fixture e2e that exercises the same Worker R2 path. Do not keep a D1-specific real-worker test in steady state.

## Local Testing Plan

Local testing is required, but it should stay mechanical. Exercise the same runtime shape as production:

```text
local SQLite -> export JSON artifacts -> seed local R2 simulation -> wrangler dev Worker -> Next dev frontend
```

Do not add a long-lived filesystem backend to the Worker. Scripts can read local files, but Worker runtime should read through the R2 binding.

```text
npm/pipeline command:
  r2_artifacts.py export
  r2_artifacts.py verify
  r2_artifacts.py publish --local

worker:
  cd worker && npx wrangler dev --local

frontend:
  NEXT_PUBLIC_TIMELINE_URL=http://localhost:8787/api npm run dev
```

Expected local checks:

- generated artifacts pass schema, row-count, bytes, sha256, latest-date, and non-empty checks.
- `GET /api/timeline`, `/api/econ`, and `/api/prices` return 200 from local R2.
- response body hash equals the local artifact hash for streamed endpoint artifacts.
- missing manifest or missing object returns an explicit error, not stale or partial data.

## Cutover Model

Avoid a long-lived `DATA_BACKEND=d1 | r2` switch. Cut over once:

```text
D1-backed production -> validated R2 preview -> R2-backed production
```

Keep the previous D1-backed Worker deployment and untouched D1 database only for a short emergency rollback window. After the window expires, delete D1 sync/serving code and the migration-only baseline files.

## Execution Plan

Implement in three PRs:

1. **Exporter + local R2 Worker:** add `r2_artifacts.py export/verify`, manifest generation, local R2 publish, and Worker R2 routes. Gate: artifacts pass schema/count/hash checks and `wrangler dev --local` serves `/api/timeline`, `/api/econ`, and `/api/prices` from local R2.
2. **Publisher + migration cutover:** add remote publish with manifest-last, single-publisher lock, upload read-back verification, price-update invocation, and one-time baseline-vs-R2 canonical parity. Gate: zero unexpected payload diffs, local-R2 frontend sanity check passes, production smoke returns 200 for `/api/timeline`.
3. **D1 cleanup:** after at least one successful unattended R2 publish and rollback-window expiry, remove D1 sync, Worker SQL, schema generation, D1 workflows/tests, and the migration-only baseline files. Gate: local SQL/debug still works through `timemachine.db`.

### Definition of done

The migration is done when:

- production data is served from R2 artifacts through the thin Worker
- D1 is no longer in the steady-state production serving or publish path
- every publish is gated by regression, artifact validation, row counts, hashes, and manifest-last semantics
- local testing can rehearse exporter -> local R2 -> Worker -> frontend without touching production
- D1-specific sync/parity/schema code has been deleted or explicitly quarantined for the short emergency rollback window only
- the frontend API surface stays narrow: timeline, econ, and lazy prices bundle

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

R2 Standard free tier includes 10 GB-month storage, 1M Class A operations/month, 10M Class B operations/month, and free egress. Daily snapshots at roughly 5 MB are about 1.8 GB/year, so storage is the only meaningful long-term cost to watch. Avoid public `r2.dev` URLs; serve private R2 objects through the Worker.

## Code Size Estimate

Expected LoC reduction is implementation-dependent. Do not make LoC the decision metric; the real simplification is removing destructive D1 sync from the production publication path.

Likely deletions after rollback-window expiry:

- D1 sync/parity scripts and tests: about 1,900 LoC
- Worker D1 SQL/schema/tests/config: about 560 LoC
- D1-specific schema/view generation and D1 workflow glue
- D1 real-worker checks, converted to R2 endpoint/local-R2 checks

Likely additions:

- JSON exporter
- R2 uploader
- manifest/hash verifier
- thin R2 Worker path
- contract/parity tests

Net should still shrink, but the main win is correctness and mental-model simplification: no mutable production database sync policy.

## Benefits

- Data publication becomes a validated manifest flip instead of mutable D1 table sync.
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
- Price publisher must not turn versioned price snapshots into full-history Yahoo refetches.

Mitigations:

- read SQLite views rather than raw tables during export
- full canonical baseline/R2 payload diff before cutover
- manifest hash and export-summary row-count verification
- fail publication if any referenced artifact is missing, empty, unparsable, or count-mismatched
- manifest cache no longer than endpoint TTL, immutable snapshot cache
- keep the previous D1-backed Worker deployment and untouched D1 database only during the short emergency rollback window
- date-keyed price merge with schema/hash validation; carry forward active rows and fetch only the missing/revision window

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
  - write object sha256 hashes into manifest
  - write row counts into export-summary.json
  - upload snapshot objects first
  - read back and verify uploaded objects
  - update manifest last
  - use migration-only baseline/R2 parity before cutover
  - delete the migration baseline after cutover confidence is established

Do not:
  - remove correctness gates just because artifacts are simpler
  - switch to minimal R2 without manifest-last publication
  - keep a long-lived `DATA_BACKEND=d1 | r2` production switch
  - keep Path A as the main plan unless production SQL becomes important again
```

Execution starts from the three-PR `Execution Plan` above:

```text
1. Exporter + local R2 Worker.
2. Publisher + migration cutover.
3. D1 cleanup after rollback-window expiry.
```
