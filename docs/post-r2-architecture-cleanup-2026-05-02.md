# Post-R2 Architecture Cleanup - 2026-05-02

## Context

The R2 JSON migration is complete. Production data is served from versioned R2 JSON artifacts through the thin Worker facade, and the old D1 database has been deleted.

Current steady-state path:

```text
local SQLite timemachine.db
  -> r2_artifacts.py export / verify
  -> versioned R2 JSON artifacts
  -> manifest.json flip
  -> Worker stream
  -> frontend Zod / compute
```

This document supersedes the earlier post-R2 cleanup draft and the separate candidates note. It is now the single source of truth for post-R2 architecture cleanup candidates.

The goal is not to chase cosmetic LoC reductions. The useful cleanups are the ones that remove obsolete contracts, private-format dependencies, one-shot subsystems, or stale docs that would mislead future work.

## Current Assessment

The main R2 migration already removed the largest architecture problem: mutable production D1 sync. There are no remaining high-risk D1 runtime dependencies.

The remaining cleanup falls into four buckets:

1. **Worth doing soon:** remove residual architecture that is now clearly obsolete.
2. **Worth doing if the product decision is clear:** remove optional operator-facing subsystems.
3. **Documentation hygiene:** remove stale D1-era instructions that would mislead future agents.
4. **Small optional simplifications:** remove duplicate checks or local temp state only if they do not weaken correctness.

## Priority Summary

| Priority | Status | Cleanup | Why | Rough reduction |
| --- | --- | --- | --- | --- |
| P1 | Done | Replace local R2 private-store publish with normal Wrangler local object ops | Removes dependency on Miniflare private SQLite/blob layout | 100-130 LoC |
| P1 | Done | Remove R2-dead fail-open `/timeline.errors` contract | Deletes a D1-era response shape that is no longer reachable | ~150 LoC |
| P1 | Done | Emit `sparkline` as JSON array, not JSON string | Moves data conversion to exporter and simplifies frontend schema | ~30 LoC |
| P1 | Done | Fix stale `AGENTS.md` / mark old D1 docs historical | Prevents future agents from following deleted D1 architecture | docs only |
| P2 | Pending | Delete one-shot `etl/migrations` package | Removes ceremonial per-build migration framework | ~280 LoC |
| P2 gated | Needs decision | Delete changelog/daily diff email subsystem | Large simplification, but only if the daily email is no longer wanted | ~2,100 LoC |
| P3 | Optional | Consider deleting live `/timeline` Zod smoke script | Duplicates publish-time artifact Zod validation, but has better CI errors | ~40 LoC |
| P3 | Optional | Consider collapsing normal automation from `export -> verify -> publish` to `export -> publish` | Avoids double verify on successful publish | small |

## P1: Replace Local R2 Private-Store Publish

### Why

`pipeline/scripts/r2_artifacts.py` still has local-publish code that writes directly into `worker/.wrangler/state/v3/r2`:

- `_local_r2_metadata_db`
- `_put_local_r2_object`
- `_verify_local_manifest_via_wrangler`
- `_publish_local_fast`

This was a pragmatic migration optimization when the artifact model briefly had one object per ticker. That pressure is gone. The final artifact set is three snapshot objects plus `manifest.json`:

- `timeline.json`
- `econ.json`
- `prices.json`
- `manifest.json`

At this size, local publishing can use the same Wrangler object path as remote publishing:

```text
wrangler r2 object put portal-data/<key> --local --file=<file>
wrangler r2 object get portal-data/<key> --local --file=<tmp>
```

### Files / changes

1. **`pipeline/scripts/r2_artifacts.py`**
   - Delete local Miniflare private-store helpers:
     - `_local_r2_root`
     - `_local_r2_metadata_db`
     - `_put_local_r2_object`
     - `_verify_local_manifest_via_wrangler`
     - `_publish_local_fast`
   - Replace local publish with the same object loop used by remote publish, passing `remote=False`.
   - Keep readback hash verification for both local and remote.

2. **Tests**
   - Update `test_r2_artifacts.py` to assert local publish calls Wrangler `put` / `get` with `--local`, rather than direct SQLite writes.
   - Keep the manifest-last ordering test.

3. **Docs**
   - Keep describing local R2 as local R2, but remove any implication that we intentionally depend on Miniflare internals.

### Verification

```bash
cd pipeline
.venv/Scripts/python.exe scripts/r2_artifacts.py export
.venv/Scripts/python.exe scripts/r2_artifacts.py verify
.venv/Scripts/python.exe scripts/r2_artifacts.py publish --local

cd ../worker
npx wrangler dev --local --port 8787
curl http://localhost:8787/api/timeline
curl http://localhost:8787/api/econ
curl http://localhost:8787/api/prices
```

Then run the local frontend/e2e flow that exercises local R2.

### Correctness impact

Positive. This removes a dependency on a private storage layout while preserving the same object readback and hash verification.

## P1: Remove the Dead Fail-Open Timeline Contract

### Why

D1's `/timeline` Worker fail-opened: each optional view (`market`, `holdings`, `txns`) was a separate live SQL query and could fail independently. The response therefore carried:

```json
{ "errors": { "market": "...", "holdings": "...", "txns": "..." } }
```

The frontend rendered explicit per-section error cards from those fields.

R2 is different. The publisher builds one complete `timeline.json` offline and fails closed at export/verify time. Today `r2_artifacts.py` always writes `"errors": {}`. That shape is now a defunct D1-era contract threaded through schema, compute, page rendering, mocks, and e2e tests.

### Files / changes

1. **`pipeline/scripts/r2_artifacts.py`**
   - Drop the `"errors": {}` field from `_build_timeline`.
   - Update tests that assert the field exists.

2. **`src/lib/schemas/timeline.ts`**
   - Delete `TimelineErrorsSchema`.
   - Delete `errors` from `TimelineDataSchema`.
   - Delete the `TimelineErrors` export.
   - Change `market` from nullable/defaulted to required:
     ```ts
     market: MarketDataSchema
     ```
   - Change `holdingsDetail` from nullable/defaulted to required:
     ```ts
     holdingsDetail: z.array(StockDetailSchema)
     ```

3. **`src/lib/schemas/index.ts`**
   - Remove the `TimelineErrors` re-export.

4. **`src/lib/compute/compute-bundle.ts`**
   - Delete `marketError`, `holdingsError`, and `txnsError` from `ComputedBundle`.
   - Delete the `data.errors.*` extraction.
   - Make `market` and `holdingsDetail` non-null in the bundle if the schema now guarantees them.

5. **`src/lib/hooks/use-bundle.ts`**
   - Delete the same error fields from the hook return type.

6. **`src/app/finance/page.tsx`**
   - Remove `marketError` destructuring.
   - Remove the unreachable "Market data failed to load" branch that depends on `errors.market`.
   - Keep the outer `ErrorBoundary`; a malformed or missing payload should fail loudly.

7. **`e2e/fail-open.spec.ts`**
   - Delete the file. It tests a response contract that no longer exists.

8. **Fixtures and mocks**
   - Remove `errors: {}` from `e2e/mock-api.ts` and any test factories.
   - Remove tests that assert per-section errors are surfaced.

### Verification

```bash
npm run test
npx playwright test
cd pipeline && .venv/Scripts/python.exe -m pytest -q
```

Grep should return no live-code matches:

```bash
rg "TimelineErrors|errors\\.market|marketError|holdingsError|txnsError" src e2e pipeline scripts
```

### Correctness impact

Positive. Missing market/holdings data becomes an export failure or schema failure, not a dead optional runtime state.

## P1: Emit `sparkline` as a JSON Array

### Why

`pipeline/etl/precompute.py` stores `computed_market_indices.sparkline` as a JSON string because the SQLite column is TEXT. The frontend currently has a Zod transform that parses this string back into `number[]`, plus a union to also accept already-parsed arrays from mocks.

R2 transports JSON natively. The exporter should parse the SQLite TEXT once and emit a real JSON array. Then the frontend schema can be plain.

### Files / changes

1. **`pipeline/scripts/r2_artifacts.py`**
   - In the market indices projection, convert `sparkline`:
     ```python
     row["sparkline"] = json.loads(row["sparkline"]) if row["sparkline"] else None
     ```
   - SQLite storage can stay as TEXT. Only the published JSON shape changes.

2. **`src/lib/schemas/timeline.ts`**
   - Delete `SparklineSchema`.
   - Change `IndexReturnSchema.sparkline` to:
     ```ts
     sparkline: z.array(z.number()).nullable().default(null)
     ```

3. **Fixtures**
   - Ensure e2e/mock fixtures emit arrays, not JSON strings.

### Verification

```bash
npm run test
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py export
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py verify
```

Spot-check:

```bash
curl https://portal.guoyuer.com/api/timeline | jq '.market.indices[0].sparkline'
```

Expected: JSON array or `null`, not a quoted JSON string.

### Correctness impact

Positive. One less runtime transform in frontend Zod, and one less mock-vs-real shape difference.

## P1: Documentation Hygiene

### Fix or remove stale `AGENTS.md`

The current untracked `AGENTS.md` still describes the old D1 architecture:

- Worker/D1 backend
- `sync_to_d1.py`
- local D1 seed flow
- D1 views and generated schema
- `/prices/:symbol`
- Worker endpoint cache

This is high-risk for future agent work because it contradicts the current R2 architecture. Either:

- replace it with the current `CLAUDE.md` architecture text, or
- delete it if it is only local scratch context.

### Archive or mark superseded docs

Some docs are now historical rather than operational:

- `docs/r2-migration-followup-cleanup-2026-05-02.md`
- `docs/code-quality-overengineering-review-2026-05-02.md`
- old D1-era plans under `docs/plans/`
- old D1-era specs under `docs/specs/`

Move them under `docs/archive/` or add a clear header:

```text
Status: Superseded by the R2 migration. Kept for historical context only.
```

Do not leave them looking like current implementation guidance.

## P2: Delete the One-Shot `etl/migrations` Package

### Why

`pipeline/etl/migrations/` contains one migration: `add_fidelity_action_kind.py`. It is invoked on every build from `pipeline/etl/build.py` and performs idempotent checks/backfill for `fidelity_transactions.action_kind`.

This made sense during transition. After all local DBs and fixtures have the column and populated values, keeping a migration framework for one historical migration is ceremony.

### Precondition

Confirm the migration has already been applied:

```bash
sqlite3 pipeline/data/timemachine.db "SELECT COUNT(*) FROM fidelity_transactions WHERE action_kind IS NULL"
```

Expected: `0`.

Also confirm regression fixtures pass:

```bash
cd pipeline
.venv/Scripts/python.exe -m pytest tests/regression/ -v
```

If either check fails, run or keep the migration until the data is clean.

### Files / changes

1. **Delete**
   - `pipeline/etl/migrations/`
   - `pipeline/tests/unit/test_fidelity_action_kind.py`

2. **`pipeline/etl/build.py`**
   - Remove the `etl.migrations.add_fidelity_action_kind` import.
   - Remove the `_migrate_fidelity_action_kind(...)` call.

3. **`pipeline/etl/db.py`**
   - Confirm `fidelity_transactions.action_kind` is part of the normal table DDL.
   - If all rows are guaranteed populated, consider tightening it from nullable to `NOT NULL`.

### Verification

```bash
cd pipeline
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/build_timemachine_db.py
```

Then confirm:

```bash
sqlite3 pipeline/data/timemachine.db "SELECT COUNT(*) FROM fidelity_transactions WHERE action_kind IS NULL"
rg "etl.migrations|_migrate_fidelity_action_kind" pipeline
```

Expected: count `0`, grep no matches.

### Correctness impact

Low risk if the precondition is satisfied. The only meaningful risk is silently keeping NULL `action_kind` rows, which the precondition and tests should catch.

## P2 Gated: Decide on Email Reporting Before Deleting Changelog

### Precondition: explicit user decision required

Do not perform this cleanup unless the user explicitly confirms they no longer want the daily diff email.

The changelog subsystem is large, but it has a real product function: showing what changed in the latest sync. If that email is still useful, keep it.

### Why

The pipeline currently captures a `SyncSnapshot` before and after every run, computes a `SyncChangelog`, renders text/html Jinja templates, and sends an SMTP email. Roughly 2,000+ LoC of code, tests, and templates serve one consumer: the daily operator email.

External monitoring can be handled by `PORTAL_HEALTHCHECK_URL`. Failure visibility can be a simpler stage-label + log-tail email.

### Files / changes if confirmed

1. **Delete**
   - `pipeline/etl/changelog/`
   - `pipeline/tests/unit/test_changelog.py`

2. **Simplify `pipeline/etl/automation/notify.py`**
   - Keep email config, low-level send, healthcheck ping, and duration formatting.
   - Replace `send_report_email` with a simpler failure-only email:
     ```text
     stage label
     exit code
     last 200 log lines
     ```

3. **Simplify `pipeline/etl/automation/runner.py`**
   - Remove `SyncSnapshot` / `capture`.
   - Remove the `_SCRIPT_OUTPUT_BUFFER` machinery if warnings no longer need special extraction.
   - Do not send success emails.
   - On failure, send the simpler failure email.

4. **Docs**
   - Update `docs/RUNBOOK.md` and `docs/automation-setup.md`.
   - Keep SMTP env vars only if failure emails still use SMTP.

### Verification

```bash
cd pipeline
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe scripts/run_automation.py --dry-run --local
```

Then manually force a failure and confirm a failure email arrives with useful log context.

### Correctness impact

No data correctness impact. This is an operator-experience decision.

## P3: Consider Deleting `scripts/validate_timeline_zod.ts`

### Why

`scripts/validate_r2_artifacts_zod.ts` already validates the exported `timeline`, `econ`, and `prices` artifacts before publish. `scripts/validate_timeline_zod.ts` adds another live-worker `/timeline` Zod check in the real-worker e2e workflow.

Value of keeping it:

- clearer CI error if the Worker serves malformed `/timeline`
- catches routing/binding mistakes before Playwright renders

Value of deleting it:

- removes one script and one CI step
- avoids duplicate Zod validation of the same payload shape

### Recommendation

Low priority. Keep it unless CI runtime or duplicate validation noise becomes annoying. It is not architecture-critical, but it is a useful early failure message.

## P3: Consider Collapsing Normal Automation Verify

### Why

`publish_artifacts()` already calls `verify_artifacts()` before upload. The unattended non-dry-run path currently runs:

```text
export -> verify -> publish
```

That verifies twice.

Possible simplified flow:

```text
export -> publish
```

Keep explicit `verify` for `--dry-run`, because dry-run should still prove artifacts without publishing.

### Trade-off

- Simpler/faster normal path.
- Slightly less granular exit-code attribution unless the runner special-cases publish-time verification failures.

### Recommendation

Low priority. The duplicate verify is conservative and cheap enough.

## Local Workspace Cleanup

These are not repo architecture changes, but they reduce noise on this machine:

1. **Remove stale ignored artifacts**

   `pipeline/artifacts/r2` can accumulate old migration snapshots, including early per-symbol snapshots that no longer match the final artifact shape.

   ```powershell
   Remove-Item -Recurse -Force pipeline\artifacts\r2
   ```

   The next export recreates it.

2. **Remove stale pytest temp state**

   `pipeline/.pytest-tmp-review` is causing a `git status` permission warning. Remove it after fixing permissions if necessary.

## Do Not Simplify

Do not remove these unless the correctness model is redesigned:

- `manifest.json` hash/byte descriptors
- remote upload readback verification
- single-publisher lock
- frontend Zod runtime parse
- publish-time Zod artifact validation
- local SQLite `timemachine.db`
- Worker 5xx fail-closed behavior for missing/invalid artifacts
- per-symbol `transactions` inside `prices.json`

These are load-bearing correctness or ergonomics boundaries. Removing them would reduce complexity by weakening the data publication guarantee or making common UI paths more expensive.

## Suggested PR Order

PR A and PR B were combined into one cleanup because they are independent, low-risk, and share the same verification path.

Completed:

1. **Combined PR A/B: local R2 publish + published JSON shape cleanup**
   - Replace private Miniflare store writes with normal Wrangler local object ops.
   - Remove fail-open `errors`.
   - Emit `sparkline` as an array.
   - Verify `r2_artifacts.py export -> verify -> publish --local`.

2. **PR D: Documentation hygiene**
   - Replace stale root `AGENTS.md` with a short pointer to `CLAUDE.md`.
   - Mark the old D1-era review/migration documents historical.

Remaining:

3. **PR C: One-shot migration cleanup**
   - Delete `etl/migrations` after confirming the precondition.

4. **PR E: Changelog/email removal**
   - Only if the daily email is no longer wanted.

5. **Optional P3 cleanup**
   - Decide on live `/timeline` Zod smoke and duplicate non-dry-run verify.

## Definition of Done

For any implemented subset:

- All changed-code grep checks show no residual references to deleted symbols.
- `cd pipeline && .venv/Scripts/python.exe -m pytest -q` passes.
- `npm run test` passes.
- `npx playwright test` passes if frontend/e2e shape changed.
- `r2_artifacts.py export -> verify -> publish --local` works.
- Production publish path remains manifest-last with readback verification.
