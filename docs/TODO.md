# Portal TODO

**Updated:** 2026-05-03 after the Fidelity/Qianji correctness audit and
historical rebaseline experiment.

Keep this file active-only. Completed refactor notes, historical decision logs,
and superseded plans live in git history unless a current doc still needs the
conclusion.

## Near-Term Cleanup

- Treat full historical recompute as an explicit data migration, not a normal
  simplification. Require a drift report and latest Fidelity positions pass
  before publishing.
- Keep incremental build semantics unless a full-build experiment proves the
  wall time and Yahoo call count are acceptable for routine automation.
- Keep `scripts/validate_api_zod.ts live` unless it becomes noisy. It duplicates
  publish-time Zod validation, but gives clearer failures in the real-worker
  workflow.

## Product Ideas

- Spending/income trends page from `qianji_transactions`.
- Monthly savings-rate chart once the desired definition is pinned down.
- Per-ticker realized P/L with FIFO lot matching. This requires real replay
  model work, not just UI.
- Allocation drift email when a category remains outside target threshold.
- Cashflow forecast from recurring Qianji transaction patterns.

## Deferred Infrastructure

- Add replay checkpoints only if build time exceeds roughly 30 seconds or
  `computed_daily` grows past roughly 5k rows.
- Add Cloudflare Logpush to R2 only after a production issue cannot be diagnosed
  with `wrangler tail`.
- Run a broader accessibility audit if someone else starts using the dashboard.
- Add parser fuzzing only after an ingest bug slips past the existing regression
  and unit tests.

## Non-Goals

- Read replicas, streaming payloads, microservices, and distributed tracing.
- Terraform/Pulumi for the current single-account Cloudflare setup.
- More giant planning docs for small refactors.
