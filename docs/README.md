# Docs

Use this directory for current operator and architecture guidance. Historical
plans and superseded audits are intentionally kept out of the active tree; use
git history for archaeology, then copy any still-valid conclusion into a
current doc.

## Current Docs

- [`../README.md`](../README.md) - project overview, architecture diagram,
  setup, and local development commands.
- [`../CLAUDE.md`](../CLAUDE.md) - source of truth for agent-facing commands,
  boundaries, and load-bearing architecture notes.
- [`ARCHITECTURE.md`](ARCHITECTURE.md) - current R2 artifact architecture,
  publication model, runtime endpoints, and correctness gates.
- [`RUNBOOK.md`](RUNBOOK.md) - manual publish, JSON shape changes, local Worker
  testing, rollback, and failure handling.
- [`automation-setup.md`](automation-setup.md) - Windows Task Scheduler setup
  for unattended local sync and publish.
- [`maintenance-simplification.md`](maintenance-simplification.md) - candidate
  plan for reducing maintenance LOC, duplicate concepts, and mental load.
- [`TODO.md`](TODO.md) - active near-term cleanup and product ideas only.

## Archive

[`archive/README.md`](archive/README.md) explains why old plans and audits are
not kept as active files.
