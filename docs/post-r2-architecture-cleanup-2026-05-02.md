# Post-R2 Architecture Cleanup - 2026-05-02

**Status:** Mostly complete. This document is now a compact closure note for the
post-R2 cleanup work, not an active migration plan.

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

## Remaining Optional Items

These are intentionally not planned unless they become annoying:

- `scripts/validate_timeline_zod.ts`: duplicates publish-time Zod validation,
  but produces clearer failures in the real-worker workflow.
- Automation double-verify: the runner does `export -> verify -> publish`, and
  `publish` verifies again. This is conservative and cheap enough.

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
