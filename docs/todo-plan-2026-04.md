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

### 2026-04-13 batch — bug fix, test audit, endpoint security migration

| PR | Branch | Summary |
|---|---|---|
| #134 | `fix/incremental-mutual-fund-weekend-floor` | `_find_price_date` walked back to `start`, not `prices.index[0]`; incremental Monday builds 302'd mutual-fund prices into `/dev/null` and dropped ~$35k from the computed_daily row. Regression test added. |
| #135 | `cleanup/audit-completion` | Executes the 6 findings in `archive/test-suite-audit-2026-04-13.md` (FRED autouse fixture, dead-fixture delete, precompute test split, interactive-check → `e2e/manual/`, behavior-named `TestBug*`, new Worker unit tests) plus the 401k warning 7-day window (Option A in `archive/401k-step-function-investigation-2026-04-12.md`). |
| #136 | `feat/worker-auth` | Adds the `REQUIRE_AUTH`/`ALLOWED_EMAIL` env-gated `isAllowedUser` + shared `src/lib/worker-auth.ts` helper. Inert by default — turns on when the dashboard migration is ready. |
| #137 | `feat/worker-custom-domains` | Custom Domains (`portal-api.guoyuer.com`, `portal-mail.guoyuer.com`) + `Access-Control-Allow-Credentials` + frontend `credentials: 'include'`. Hit the cross-subdomain cookie wall in production and needed #138. |
| #138 | `fix/api-same-origin` | Retire the `portal-api.guoyuer.com` Custom Domain path; mount portal-api as a zone route on `portal.guoyuer.com/api/*` so the existing Access cookie authenticates API calls. |
| #139 | `feat/gmail-same-origin` | Same move for worker-gmail browser paths (`portal.guoyuer.com/api/mail/*`). Drops `USER_KEY` secret + `X-Mail-Key` header + frontend localStorage key path + `keyMissing` UI. |
| #140 | `cleanup/post-migration` | Archive the security doc; delete `worker-auth` module + `isAllowedUser` + `unauthorized` + `REQUIRE_AUTH/ALLOWED_EMAIL` vars (Access gates everything — defense-in-depth was inert); strip `credentials: "include"` from frontend; remove failing CI `Deploy Worker` + `Apply D1 schema` steps (token scope issue); CLAUDE.md note on Git Bash MSYS path mangling. |
| #141 | `fix/worker-gmail-path-lockdown` | Match `/api/mail/list` / `/api/mail/trash` / `/mail/sync` literally instead of strip-and-match. Makes `Portal Mail` Access app truly orphan — safe to delete. |

Dashboard side (CLI-driven via the scoped setup token, then self-revoked): created/updated Access apps, deleted the `portal-api.guoyuer.com` Custom Domain, retired the `Portal API EMERGENCY LOCK` deny-all placeholder. `.workers.dev` closed on both Workers (automatic once `routes` are present in wrangler config).

### 2026-04-14 batch — audit-driven cleanup

Follow-up from `docs/code-design-audit-2026-04-13.md`:

| PR | Branch | Summary |
|---|---|---|
| (C01+C02) | `refactor/unify-env-url-convention` | `NEXT_PUBLIC_TIMELINE_URL` is now a *base* URL (`https://portal.guoyuer.com/api`), same shape as `NEXT_PUBLIC_GMAIL_WORKER_URL`. Deletes the `src/lib/config.ts` regex-strip; test fixture + playwright + CI fallback + README + CLAUDE.md updated. GH secret value updated to match. |
| (C04) | `docs/allocation-dataclass-hint` | Top-of-file comment in `pipeline/etl/allocation.py` pointing at `AllocationRequest` dataclass as the migration target when the 7th data source lands. No behavior change. |
| (C01 redux) | `claude/fix-mail-fetch-error-BhWXz` | Second firing of the same class of bug — "/mail: Failed to fetch" after the CORS drop in PR #146. Root cause wasn't just env-var shape (C01/C02's focus) but that **one GH secret (`PORTAL_GMAIL_WORKER_URL`) was serving two consumers with incompatible values**: cron wants the external `portal-mail.guoyuer.com`; frontend wants the same-origin `/api/mail`. CI baked the cron's value into the browser bundle. Fix: (a) drop the frontend CI override — code default `/api/mail` is the only correct prod value; (b) rename the secret to `PORTAL_GMAIL_CRON_URL` so the dual-audience confusion can't recur; (c) `??` → `\|\|` in `use-mail.ts` + `config.ts` so an empty GH secret (which arrives as `""`, not `undefined`) still falls back. |

---

## 4. Not doing (explicit)

- **CNY manual_rates.csv seed file** (structural plan item 12): abandoned. Replaced by PR-X invariant protection. Yahoo has full history; the "missing data" was transient flakiness — seed file was treating a symptom, not the cause.
- **Force-resync old Adj Close era data from prod to local**: prod is correct; local is stale. Fix is local rebuild, not reverse-sync.

---

## Deferred ideas

Real improvements, but no near-term commitment. Don't start without a design conversation. Organized into two tracks: *audit-surfaced* (came out of the 2026-04-13 post-migration review) vs. *strategic* (pre-existing ideas that predate the migration).

| Audit-surfaced (from `docs/code-design-audit-2026-04-13.md`) | Strategic (pre-existing) |
|---|---|
| **C03** — Promote `log.warning` in `_add_fidelity_positions:136` to a fatal when the ticker's held value > $1000 AND the price is missing. Low-cost insurance against the next weekend-floor-class bug. (~20 LoC) | **Two-column `daily_close` (close + adj_close)** — store both Yahoo Close and Adj Close; delete `_reverse_split_factor`. ~−28 LoC net, but needs D1 ALTER + backfill + picking which column is canonical. |
| **C04** — Dataclass refactor of `compute_daily_allocation` signature (6 positional → `AllocationRequest`). Trigger: when the 7th data source lands. Top-of-file comment now points at this as the migration target. (~40 LoC) | **Yahoo-fetch retry + validation layer** — assert returned dates cover the requested range, retry 2–3 times, raise on final failure. Complements the PR-X daily_close invariant. |
| **C05** — Split `pipeline/etl/timemachine.py` (602 LoC) into `etl/replay/fidelity.py` + `etl/replay/qianji.py` + `etl/replay/__init__.py`. Trigger: next real change on either replay. (0 net LoC, move-only) | **SQL-pushdown for hot compute paths** — per-date category aggregation via `SUM(CASE WHEN category=…)`; 52w high/low via SQL window function. Profile first. |
| **C06** — Accept Python `types.py` ↔ TS `src/lib/schemas/` duplication. Cost of codegen > value at current schema-churn rate. | |
| **C07** — Replace Worker if-ladder routing with [itty-router](https://itty.dev/itty-router/) or Hono. Trigger: next endpoint added on either Worker. (~30 LoC added) | |

---

## 6. Open questions

1. Is "invariant protection + retry" enough for `daily_close` correctness, or is the two-column migration worth doing?
2. When (if ever) to invest in the SQL-pushdown pipeline speedups — after profiling confirms a real bottleneck?
