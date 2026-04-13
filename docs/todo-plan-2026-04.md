# TODO & Plan — 2026-04

Master tracking for outstanding work. Supersedes ad-hoc notes; `archive/structural-cleanup-plan-2026-04.md` is the detailed reference for structural items 1–11.

---

## 0. Invariants and guiding principles

These are the contracts we now enforce; any change below must preserve them.

1. **Historical data is immutable.** `daily_close.close` (unadjusted market close) is a physical fact. Rows older than the refresh window (7 days) must not be overwritten by subsequent fetches.
2. **Rebuild from raw inputs + repo seeds must always succeed.** No reliance on local-only state in someone's SQLite file.
3. **Fail loudly at boundaries.** Schema drift, empty Yahoo response, malformed dates — raise with a clear message, do not silently corrupt.
4. **Clean refactors, no backcompat shims.** Delete old code paths when replacing them.
5. **Don't hide errors in UI.** Failed sections render explicit error cards.

---

## 1. Completed

Batch 4 structural-cleanup PRs (2026-04-12):

| PR | Branch | Items |
|---|---|---|
| #98 | `fix/prices-invariant-protection` | PR-X: daily_close invariant protection (IGNORE historical, REPLACE recent) |
| #99 | `refactor/structural-quick-wins` | PR-A: items 1, 2, 3, 8, 10, 11 |
| #100 | `refactor/pipeline-ingest-reorg` | PR-C: item 5 — `ingest_*` out of `db.py` |
| #101 | `refactor/frontend-restructure` | PR-B: items 4, 6, 7 — schemas/, shared.tsx split, style files |
| #102 | `refactor/build-script-split` | PR-D: `_ingest_and_fetch` → 4 named helpers |
| #103 | `fix/fidelity-ingest-natural-key` | PR-E: INSERT OR IGNORE + natural-key dedup + `init_db` migration |

Automation-readiness follow-ups (PRs #109–#114) landed shortly after — see `archive/plan-automation-readiness-2026-04-12.md` for the execution record.

---

## 4. Not doing (explicit)

- **CNY manual_rates.csv seed file** (structural plan item 12): abandoned. Replaced by PR-X invariant protection. Yahoo has full history; the "missing data" was transient flakiness — seed file was treating a symptom, not the cause.
- **Force-resync old Adj Close era data from prod to local**: prod is correct; local is stale. Fix is local rebuild, not reverse-sync.

---

## Deferred ideas

Real improvements, but no near-term commitment. Don't start without a design conversation.

- **Two-column `daily_close` (close + adj_close)** — store both Yahoo Close and Adj Close; delete `_reverse_split_factor`. Requires schema migration across local + D1.
- **Retry + validation layer for Yahoo fetches** — assert returned dates cover the requested range, retry 2–3 times, raise on final failure. Complements PR-X.
- **SQL-pushdown for hot compute paths** — per-date category aggregation via `SUM(CASE WHEN category=…)`; 52w high/low via SQL window function. Profile first.

---

## 6. Open questions

1. Is "invariant protection + retry" enough for `daily_close` correctness, or is the two-column migration worth doing?
2. When (if ever) to invest in the SQL-pushdown pipeline speedups — after profiling confirms a real bottleneck?
