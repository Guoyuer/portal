# Post-R2 Architecture Cleanup - 2026-05-02

**Status:** Mostly complete. This document is now a compact closure note for the
post-R2 cleanup work, plus a short list of optional simplification candidates.

## Current Architecture

Production data now follows the R2 artifact path:

```text
local SQLite timemachine.db
  -> r2_artifacts.py export / verify
  -> versioned R2 JSON artifacts
  -> manifest.json flip
  -> Worker stream
  -> frontend Zod / compute
```

The old destructive D1 publish path is gone. The remaining code should preserve
that correctness model: local build gates, artifact hashes, row counts, Zod
validation, readback verification, single-publisher locking, and manifest-last
publication.

## Completed Cleanup

| Area | Result |
| --- | --- |
| Local R2 publish | Replaced Miniflare private-store writes with normal Wrangler local R2 object ops. |
| Timeline shape | Removed the dead fail-open `errors` contract from R2-era `/timeline`. |
| Market sparkline | Published `sparkline` as a JSON array instead of a string requiring frontend parsing. |
| Docs hygiene | Replaced stale root `AGENTS.md` with a pointer to `CLAUDE.md`; old D1 docs are historical. |
| One-shot migrations | Deleted the obsolete `etl/migrations` package. |
| Daily email | Kept daily mail, but simplified it into a publish receipt instead of a semantic audit subsystem. |
| Old plans/specs | Archived superseded `docs/plans/` and `docs/specs/` content. |
| TODO | Replaced the completed review checklist with a short active-only TODO. |

## Simplification Review - 2026-05-02

This follow-up review looked specifically for unnecessary intermediate layers,
compatibility schemas, and naming residue left after the R2 migration. The main
architecture is in good shape; the useful cleanups are mostly small boundary
tightening tasks.

| Priority | Status | Cleanup | Why |
| --- | --- | --- | --- |
| P2 | Done | Emit `/econ.series` as arrays, not JSON strings | Removes the last frontend JSON-string compatibility schema. |
| P2 | Done | Export SQLite booleans as JSON booleans | Stops leaking SQLite 0/1 storage into frontend schemas. |
| P3 | Done | Tighten frontend Zod defaults on required artifact fields | Makes schema drift fail loudly instead of silently filling missing arrays. |
| P3 | Done | Rename publish email code to receipt/reporting module | Aligns naming with the simplified email role. |
| P3 | Deferred | Harden or prune conditional mock e2e checks | Worth doing only with fixture-specific assertions; avoid making smoke tests brittle. |

### Emit `/econ.series` as JSON arrays

Status: implemented. `pipeline/scripts/r2_artifacts.py` builds `econ.series` with
`json_group_array(...)`, which returns a JSON-encoded string per series.
R2 transports JSON natively, so the exporter now `json.loads` each series and
publishes real arrays. The frontend schema validates `EconPoint[]` directly.

Verification:

```bash
npm run test
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py export
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py verify
```

### Export SQLite booleans as JSON booleans

Status: implemented. `QianjiTxn.isRetirement` is stored as SQLite INTEGER 0/1,
but R2 artifacts are JSON API payloads, not SQLite rows. The exporter now
converts `isRetirement` to a real boolean, and `pipeline/tools/gen_zod.py` no
longer needs a `coerce_bool` mode.

Verification:

```bash
cd pipeline && .venv/Scripts/python.exe tools/gen_zod.py --write ../src/lib/schemas/_generated.ts
npm run test
cd pipeline && .venv/Scripts/python.exe -m pytest -q
```

### Tighten Zod defaults on required artifact fields

Status: implemented. These exporter-guaranteed fields no longer default missing
values to empty arrays/records:

- `TimelineDataSchema.dailyTickers`
- `TimelineDataSchema.fidelityTxns`
- `TimelineDataSchema.qianjiTxns`
- `TickerPriceResponseSchema.prices`
- `TickerPriceResponseSchema.transactions`
- `EconDataSchema.series`

Under the current R2 publication model, missing fields mean artifact/schema
drift and fail loudly. Nullability remains only for fields that are genuinely
optional in the published API, such as nullable market values.

### Rename publish email code to receipt/reporting

Status: implemented. The old changelog subsystem produced semantic row-level
diffs. After the email simplification, the code now lives in
`etl.automation.receipt` and formats a compact publish receipt: artifact
version, latest date, object sizes, aggregate row deltas, latest net worth,
warnings, duration, and failure stage.

### Harden or prune conditional mock e2e checks

Status: deferred. `e2e/finance.spec.ts` still contains many conditional returns such as "if no
table, return" or "if market card does not render, return". That is reasonable
for smoke tests against variable data, but the mock API fixture is controlled.
Mock regression tests should assert the expected fixture state or be deleted.

Do this only as a fixture-specific Playwright cleanup. It is not a data
correctness gate, and making broad UI smoke tests brittle is not a useful trade.

## Remaining Optional Items

These are intentionally not planned unless they become annoying:

- `scripts/validate_live_api_zod.ts`: duplicates publish-time Zod validation,
  but produces clearer failures in the real-worker workflow.
- Automation double-verify: the runner does `export -> verify -> publish`, and
  `publish` verifies again. This is conservative and cheap enough.

## Suggested PR Order

1. Replace silent returns in `e2e/finance.spec.ts` with deterministic fixture
   assertions, or delete duplicated weak smoke tests.
2. Decide on the two low-priority optional items only if they start adding
   runtime or maintenance noise.

## Do Not Simplify

Do not remove these without redesigning the correctness model:

- `manifest.json` hash and byte descriptors
- remote upload readback verification
- single-publisher lock
- frontend Zod runtime parse
- publish-time Zod artifact validation
- local SQLite `timemachine.db`
- Worker fail-closed behavior for missing or invalid artifacts
- per-symbol transactions inside `prices.json`

Those boundaries are load-bearing. Removing them would reduce code by weakening
data publication correctness or making the UI path more expensive.
