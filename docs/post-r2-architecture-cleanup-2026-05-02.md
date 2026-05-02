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
| P2 | Proposed | Emit `/econ.series` as arrays, not JSON strings | Removes the last frontend JSON-string compatibility schema. |
| P2 | Proposed | Export SQLite booleans as JSON booleans | Stops leaking SQLite 0/1 storage into frontend schemas. |
| P3 | Proposed | Tighten frontend Zod defaults on required artifact fields | Makes schema drift fail loudly instead of silently filling missing arrays. |
| P3 | Proposed | Rename `etl.changelog` to publish receipt/reporting module | Aligns naming with the simplified email role. |
| P3 | Proposed | Harden or prune conditional mock e2e checks | Converts weak smoke tests into deterministic fixture assertions. |

### Emit `/econ.series` as JSON arrays

`pipeline/scripts/r2_artifacts.py` builds `econ.series` with
`json_group_array(...)`, which returns a JSON-encoded string per series.
`src/lib/schemas/econ.ts` then accepts both strings and arrays via a union and
transform. R2 transports JSON natively, so the exporter should `json.loads` each
series and publish real arrays. The frontend schema can then validate
`EconPoint[]` directly.

Verification:

```bash
npm run test
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py export
cd pipeline && .venv/Scripts/python.exe scripts/r2_artifacts.py verify
```

### Export SQLite booleans as JSON booleans

`QianjiTxn.isRetirement` is stored as SQLite INTEGER 0/1, and the generated Zod
schema currently accepts `boolean | number` with a transform. R2 artifacts are
JSON API payloads, not SQLite rows. Convert `isRetirement` to a real boolean in
the exporter, then remove `coerce_bool` from `pipeline/tools/gen_zod.py` if no
other exported field needs it.

Verification:

```bash
cd pipeline && .venv/Scripts/python.exe tools/gen_zod.py --write ../src/lib/schemas/_generated.ts
npm run test
cd pipeline && .venv/Scripts/python.exe -m pytest -q
```

### Tighten Zod defaults on required artifact fields

Some schemas still default missing exporter-guaranteed fields to empty values:

- `TimelineDataSchema.dailyTickers`
- `TimelineDataSchema.fidelityTxns`
- `TimelineDataSchema.qianjiTxns`
- `TickerPriceResponseSchema.prices`
- `TickerPriceResponseSchema.transactions`
- `EconDataSchema.series`

Those defaults made sense when the frontend was defensive against partial Worker
responses. Under the current R2 publication model, missing fields mean
artifact/schema drift and should fail loudly. Keep nullability only for fields
that are genuinely optional in the published API, such as nullable market
values.

### Rename `etl.changelog` to publish receipt/reporting

The old changelog subsystem produced semantic row-level diffs. After the email
simplification, `pipeline/etl/changelog/__init__.py` formats a compact publish
receipt: artifact version, latest date, object sizes, aggregate row deltas,
latest net worth, warnings, duration, and failure stage. The name now overstates
its job. Move it to something like `etl.automation.report` or
`etl.automation.receipt` and update stale "changelog email" wording.

### Harden or prune conditional mock e2e checks

`e2e/finance.spec.ts` still contains many conditional returns such as "if no
table, return" or "if market card does not render, return". That is reasonable
for smoke tests against variable data, but the mock API fixture is controlled.
Mock regression tests should assert the expected fixture state or be deleted.

## Remaining Optional Items

These are intentionally not planned unless they become annoying:

- `scripts/validate_timeline_zod.ts`: duplicates publish-time Zod validation,
  but produces clearer failures in the real-worker workflow.
- Automation double-verify: the runner does `export -> verify -> publish`, and
  `publish` verifies again. This is conservative and cheap enough.

## Suggested PR Order

1. Remove remaining published-shape compatibility:
   - emit `/econ.series` as arrays
   - export Qianji `isRetirement` as a JSON boolean
   - tighten Zod defaults only for exporter-guaranteed fields
2. Rename `etl.changelog` to a publish receipt/reporting module.
3. Replace silent returns in `e2e/finance.spec.ts` with deterministic fixture
   assertions, or delete duplicated weak smoke tests.
4. Decide on the two low-priority optional items only if they start adding
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
