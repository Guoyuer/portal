# Docs

Use this directory for current operator and architecture guidance. Historical
plans and superseded audits live under `archive/` and should not be used as
implementation guidance unless a current doc links to them for context.

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
- [`TODO.md`](TODO.md) - active near-term cleanup and product ideas only.

## Archive

[`archive/`](archive/) contains completed plans, dated audits, and migration
notes retained for historical reference. Start with
[`archive/README.md`](archive/README.md) before opening individual archived
files.
