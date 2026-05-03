# Maintenance Simplification Plan

This is the working plan for reducing maintenance LOC, duplicate concepts, and
mental load after the R2 migration and PR #303 cleanup. It is intentionally
separate from `TODO.md`: this file is the candidate backlog, while `TODO.md`
stays active-only.

## Baseline

Raw tracked repo size is about 266 files / 42.0k physical LOC. The maintenance
surface below excludes lockfiles, `docs/archive/`, generated Zod, and the
golden regression fixture:

| Area | Files | LOC | Share |
| --- | ---: | ---: | ---: |
| pipeline tests | 50 | 7,175 | 26.7% |
| frontend app/lib | 64 | 6,004 | 22.3% |
| pipeline etl | 42 | 5,704 | 21.2% |
| frontend tests | 30 | 2,729 | 10.1% |
| pipeline scripts/tools | 9 | 1,332 | 5.0% |
| root/config/misc | 36 | 1,317 | 4.9% |
| e2e tests | 6 | 809 | 3.0% |
| pipeline fixtures | 11 | 783 | 2.9% |
| docs current | 6 | 426 | 1.6% |
| CI | 4 | 337 | 1.3% |
| worker source | 3 | 275 | 1.0% |

Total: 261 files / 26.9k physical LOC after the duplicate-test compression,
excluding the same generated/archive/fixture surfaces.

Use the same exclusion rule when reporting future LOC deltas:

```powershell
$exclude = '^(package-lock\.json$|worker/package-lock\.json$|docs/archive/|src/lib/schemas/_generated\.ts$|pipeline/tests/fixtures/regression/golden\.json$)'
git ls-files |
  ForEach-Object { $_.Trim() } |
  Where-Object { $_ -and $_ -notmatch $exclude } |
  ForEach-Object {
    if (Test-Path -LiteralPath $_ -PathType Leaf) {
      (Get-Content -LiteralPath $_ | Measure-Object -Line).Lines
    }
  } |
  Measure-Object -Sum
```

The repo is not large because of one oversized application file. It is large
because correctness is spread across ETL replay, artifact publication, frontend
compute, and tests. The best reductions should remove duplicate representations
or entire rare paths, not just move code around.

## Guardrails

Do not reduce these unless the data-publication model is redesigned:

- `manifest.json` hash and byte descriptors.
- Remote upload readback verification.
- Single-publisher lock.
- Publish-time Zod artifact validation.
- Frontend runtime Zod parse in `use-timeline-data.ts`.
- Local SQLite `timemachine.db` as the source of truth.
- Worker fail-closed behavior for missing or invalid artifacts.
- Per-symbol transactions inside `prices.json`.

Do not chase LOC by splitting files into more files, replacing explicit source
logic with clever metaprogramming, or adding framework abstractions. Prefer
deleting flows, narrowing outputs, and using table-driven tests.

## Candidate Reductions

| Priority | Area | Candidate | Expected LOC | Risk | Recommendation |
| --- | --- | --- | ---: | --- | --- |
| S1 | Automation email | Simplify `receipt.py` and `notify.py`: keep text receipt as source of truth, make HTML a plain `<pre>`, reduce row-delta details, and report artifact summary first. | Done | Low | Receipt now derives from before/after snapshots; email formatting no longer models row deltas separately. |
| S2 | Automation warning capture | Replace log-file fallback parsing with per-run subprocess buffer only, or keep fallback in tests only. | Done | Low | Runner passes the current subprocess buffer; old log-file parsing was removed. |
| S3 | Build orchestration | Collapse repeated full/incremental tail logic in `build.py`, or remove incremental mode if full build stays fast enough. | -100 to -300 | Medium | Measure build time first. Keep incremental only if it buys real runtime. |
| S4 | Test style | Convert large Python tests to builders and parametrized cases, especially prices, allocation, Qianji, build orchestration, and automation. | Partial | Low-Medium | Prices, allocation, automation, Qianji, receipt, and replay tests are compressed; build orchestration can still shrink later. |
| S5 | Frontend compute tests | Compress repeated `compute.test.ts` scenarios with shared fixtures and table-driven expectation helpers. | Done | Low | Coverage retained with table-driven helpers and fewer repeated assertions. |
| S6 | Ticker/group data | Deduplicate chart data-state helpers across ticker and group views. Keep source-specific transaction semantics outside the chart shell. | -150 to -400 | Medium | Worth doing only if the shared shell stays small and obvious. |
| S7 | Finance UI tables | Reuse table row/header helpers across allocation, ticker, transaction, and group tables where markup is identical. | -100 to -250 | Medium | Small win. Avoid a generic mega-table abstraction. |
| S8 | R2 artifact script | Extract endpoint descriptor metadata once: path, schema name, row-count key, and validation summary. Share it across export, verify, summary, and Zod smoke helpers. | Partial | Medium | Endpoint write/descriptor verification is deduped; row-count metadata can still be consolidated later. |
| S9 | Validation CLIs | Merge old artifact/live Zod scripts behind one small `validate_api_zod.ts` CLI with `live` and `artifacts` modes. | Done | Low | Keep the real-worker failure messages readable. |
| S10 | Manual e2e paths | Consolidate `e2e/manual/*` and manual Playwright config into one documented smoke/perf command. | Done | Low | Removed the manual screenshot/perf specs and config; mock e2e, real-worker e2e, and ticker cluster unit coverage remain. |
| S11 | Config example | Shrink `pipeline/config.example.json` to a minimal template with representative assets and all supported config keys. | Done | Low-Medium | Add every real held ticker to private `config.json`; unknown holdings still fail closed. |
| S12 | Docs archive | Move `docs/archive/` to a branch/wiki or keep only an archive index plus the few decision records still referenced. | Done | Low | Historical notes were removed from the active tree; use git history for archaeology. |
| S13 | Qianji legacy fallback | Review old CNY and category fallback logic; delete branches covered by newer source invariants. | -80 to -180 | Medium | Only after regression fixtures prove old exports do not need them. |
| S14 | Source modules | Deduplicate tiny broker helpers such as date normalization, download discovery, and CUSIP lookup. | -50 to -150 | Medium | Keep broker-specific parsing explicit. Do not force all sources into one generic parser. |
| S15 | CI workflows | Fold rare baseline refresh and real-worker workflows if they are not pulling their weight. | -50 to -150 | Low | Only if operational signal remains clear. CI LOC is already small. |
| S16 | Worker | No meaningful LOC target. | 0 | Low | At 157 LOC, leave it boring and explicit. |

## Highest-Leverage Waves

Initial execution pass completed: S1 receipt-state simplification, S2
buffer-only warning capture, S4 selected Python test compression, S5 compute
test compression, S9 CLI merge, S11 config template shrink, ResourceWarning
cleanup, pytest xdist enablement, S10 manual-e2e deletion, and part of S8
endpoint descriptor dedup.

Latest S4 follow-up: prices/allocation/automation tests were pruned for true
duplicate coverage (`existing CNY row` vs gap-fill, recent-window fetch vs
refresh-window assertion, no-position CSV publish vs all-ok publish, and basic
allocation vs categorization). Redundant long-form test prose was also removed.
Net effect: 3 test files, 86 insertions / 403 deletions (`-317 LOC`), with the
Python gate still passing at 522 tests and 94.88% ETL coverage.

### Wave 1: Safe Deletions and Test Compression

Targets:

- S1 automation receipt/notify simplification.
- S4 Python test builders and parametrization.
- S5 frontend compute test parametrization.
- Existing `ResourceWarning` cleanup from `TODO.md`.

Expected effect: roughly -1.2k to -2.8k maintenance LOC with low product risk.
This wave attacks the largest current LOC buckets without changing data shape.

### Wave 2: Duplicate Contract Removal

Targets:

- S3 build orchestration full vs incremental decision.
- S8 remaining row-count and validation-summary metadata consolidation.

Expected effect: roughly -400 to -1k maintenance LOC. More importantly, it
reduces duplicate contract knowledge: endpoint names, row counts, artifact
schemas, and config examples should be defined fewer times.

### Wave 3: Feature Surface Decisions

Targets:

- S6 ticker/group chart shell dedup, or cut low-value group drilldown paths.
- S7 finance table helper reuse.
- S13 Qianji legacy branch review.

Expected effect: small-to-medium LOC reduction, but useful mental-load reduction
if rarely used surfaces are deleted instead of polished.

## Specific Deduplication Targets

The following concepts are currently repeated enough to deserve attention:

- Endpoint artifact metadata: `timeline`, `econ`, `prices`, their paths,
  schemas, row-count summaries, and smoke-test labels.
- Email receipt data: DB row labels, artifact summary labels, subject details,
  and HTML/text rendering.
- Test fixture setup: temp DB creation, config scaffolding, price CSV rows,
  Qianji rows, and allocation assertions.
- Chart load states: loading, parse error, empty data, selected symbol/group,
  and transaction overlays.
- Finance table chrome: sticky headers, numeric alignment, percent/money
  formatting, empty states, and expandable row affordances.
- Build date-window handling: refresh start, gap-fill start, and as-of overrides.

## Non-Targets

These areas look tempting but should not be simplified for LOC alone:

- Worker routing and R2 streaming: small and load-bearing.
- Investment source protocol: explicit source modules keep broker quirks local.
- Generated Zod schemas: generated code is not maintenance surface.
- Publish verification stages: some duplication is intentional defense in depth.
- Manual SQL debuggability through SQLite: important for financial data audits.

## Definition of Done

Each simplification PR should include:

- Maintenance LOC delta using the same exclusion rules as the baseline.
- A short note about which duplicate concept was removed.
- No reduction to artifact publication correctness gates.
- Relevant tests passing for the touched surface.
- Full validation before merge for broad ETL or frontend compute changes:

```bash
cd pipeline && .venv/Scripts/python.exe -m pytest -q -n 4
cd pipeline && .venv/Scripts/python.exe -m mypy etl/ --strict --ignore-missing-imports
cd pipeline && .venv/Scripts/python.exe -m ruff check .
npm run test:coverage
npm run lint
npm run build
npx playwright test
```
