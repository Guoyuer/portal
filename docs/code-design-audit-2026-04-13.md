# Code Design Audit — 2026-04-13

**Scope:** Design-level smells surfaced while working through today's 9 PRs (bug fix #134, test audit #135, endpoint security migration #136-#141, docs refresh #142). Each finding has file:line evidence and is ranked by whether it has *already* caused a bug today vs. whether it's a latent pain point.

Focused on **things that actually bit** (or nearly bit) plus **growing pains**. Excludes pure cosmetics and one-off grumbles.

---

## 🔴 Already caused a bug today

### [C01] Env var URL conventions are inconsistent — HIGH ROI ★

**Verified.** Two frontend env vars describe Worker URLs but expect *different shapes*:

| Var | Expected value | Fetch pattern | Consumer |
|---|---|---|---|
| `NEXT_PUBLIC_TIMELINE_URL` | `https://portal.guoyuer.com/api/timeline` (full URL, **including** `/timeline` suffix) | `${TIMELINE_URL}` directly, or regex-strip to derive `WORKER_BASE` | `src/lib/config.ts:1` |
| `NEXT_PUBLIC_GMAIL_WORKER_URL` | `/api/mail` (path prefix, **excluding** `/list` suffix) | `${MAIL_BASE}/list` | `src/lib/use-mail.ts:19` |

The first is "a full endpoint URL that the code regex-strips back to a base." The second is "a base URL the code appends endpoint names to." Same-domain problem, opposite conventions.

**How it bit today:** When we cut worker-gmail over to same-origin, I forgot the second convention and initially set `NEXT_PUBLIC_GMAIL_WORKER_URL=https://portal-mail.guoyuer.com` — the old full-URL-minus-`/mail` shape. That worked for the OLD code (`${WORKER_URL}/mail/list`). The new code (`${MAIL_BASE}/list`) interpreted it as "base URL" → fetches to `https://portal-mail.guoyuer.com/list` (no `/mail/`). 404. The /mail page showed "Failed to fetch" until I updated the secret to `/api/mail`.

- LoC impact: **+5 / −10** (normalize both to be "base URL, no endpoint suffix"; delete the regex)
- Risk: **L** (mechanical, has tests)
- Primary: eliminate a class of human-memory bug

**Action:**
1. Make `NEXT_PUBLIC_TIMELINE_URL` a base URL too (`https://portal.guoyuer.com/api`).
2. `src/lib/config.ts`:
   ```ts
   export const WORKER_BASE = process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787";
   export const TIMELINE_URL = `${WORKER_BASE}/timeline`;
   export const ECON_URL = `${WORKER_BASE}/econ`;
   ```
   No regex. Both env vars now say the same thing in the same shape.
3. Update the GH secret value.
4. Delete `src/lib/config.test.ts` line 16 fixture (now irrelevant).

---

### [C02] `WORKER_BASE` regex-strip is brittle

**Verified.** `src/lib/config.ts:1`:

```ts
export const WORKER_BASE = (process.env.NEXT_PUBLIC_TIMELINE_URL ?? "http://localhost:8787/timeline").replace(/\/timeline$/, "");
```

Strips `/timeline` from the env var, then `TIMELINE_URL`, `ECON_URL`, etc. re-append path names. Failure modes:

- Env var ends with `/timeline-v2` or `/timeline/` → regex misses, derived URLs have stray `/timeline` in them.
- Env var ends with `.json` or any other endpoint name → no strip, the derived ECON_URL becomes `…/timeline/econ`.
- Forces the env var convention to *always* include `/timeline`, which is what bit us in C01.

Subsumed by C01's fix.

---

### [C03] `compute-range start` leaking into data-lookup helpers — pattern check

**Verified the instance we already fixed** (PR #134, `_find_price_date` used the compute `start` as floor → lost $35k on Monday builds). Worth a quick pass to confirm no sibling bugs.

Suspect pattern: `_add_fidelity_positions`, `_add_qianji_balances`, `_add_401k`, `_add_robinhood` in `allocation.py:123-208` all take `price_date` / `mf_price_date` / `cny_rate` computed by `_resolve_date_windows`. If the caller passed bogus dates, the helpers happily look up whatever's at those dates (or warn + drop). No self-validation.

**What's actually risky:** the *other* walk in `_resolve_date_windows` (CNY rate walk, allocation.py:111-115) had the same `while d > start` bound — I fixed it alongside in PR #134. So both walks are patched.

**What's not a current risk:** the `_add_*` helpers are pure lookups given valid dates; they don't have their own walk-back logic. But their failure mode (silent drop + warning) is the one that lost $35k — lookups should probably *error* on a missing price for a currently-held position, not warn.

- LoC impact: small (~20 LoC to promote the warning to a fatal when the ticker has value > threshold)
- Risk: **L** (just tighter guardrails)
- Primary: catch the next weekend-floor-class bug at build time, not via email

**Action (optional):** in `_add_fidelity_positions:136`, if `qty * last_known_price > $1000` AND `p_date not in prices.index`, raise instead of `log.warning`. Let the validation gate catch it. Low-cost insurance.

---

## 🟡 Growing pains

### [C04] `compute_daily_allocation` signature is getting wide

**Verified.** `pipeline/etl/allocation.py:250`:

```python
def compute_daily_allocation(
    db_path: Path,
    qj_db: Path,
    config: dict[str, object],
    k401_daily: dict[date, dict[str, float]],
    start: date,
    end: date,
    *,
    robinhood_csv: Path | None = None,
) -> list[dict[str, object]]:
```

6 positional + 1 kwarg. Every new data source adds a positional. Tests invoke it positionally so renaming is painful. The 5 `_add_*` helpers inside all **mutate `ticker_values` dict** as side-effect (allocation.py:143, 181, 195, 207, etc.); no return value. Works but makes unit-testing a single source in isolation clumsy (you always assemble the whole dict first).

- LoC impact: ~40 LoC refactor (dataclass `AllocationRequest` + helpers return `dict[str, float]` instead of mutating)
- Risk: **M** (touches many call sites but mostly mechanical)
- Primary: new data sources slot in cleanly; per-source tests become trivial

**Action:** defer until we actually need a 7th data source. Put a comment at the top of `allocation.py` noting the dataclass would be the migration target.

---

### [C05] `timemachine.py` is 602 LoC, mixing three concerns

**Verified.** `pipeline/etl/timemachine.py` currently holds:
- Fidelity replay (`replay_from_db`, `_apply_transaction`, position tracking)
- Qianji replay (`replay_qianji`, `replay_qianji_currencies`)
- Date parsing helpers (`_parse_date`, format conversions)
- Shared constants (`MM_SYMBOLS`, `POSITION_PREFIXES`)

Not broken — just a dumping ground. Future Fidelity bug → edit timemachine.py, Qianji bug → edit timemachine.py, etc. Each has its own test file (`test_timemachine.py`) but the source is monolithic.

- LoC impact: 0 net (split only)
- Risk: **L** (move-only, no behavior change)
- Primary: smaller blast radius when editing one replay

**Action:** defer; split into `etl/replay/fidelity.py`, `etl/replay/qianji.py`, `etl/replay/__init__.py` when the next real change lands on either side.

---

### [C06] Two-copy type definitions (Python `types.py` ↔ TS `src/lib/schemas/`)

**Verified.** Every pipeline → Worker → frontend field has to be declared twice:

- Python `pipeline/etl/types.py` — TypedDicts, used by builder + sync
- TypeScript `src/lib/schemas/*.ts` — Zod schemas, used by Worker + frontend

Synced by discipline. CLAUDE.md has a "Type contract" section. No automation.

- LoC impact: negative long-term (delete one set); positive short-term (codegen infra)
- Risk: **M** (cross-language, build-pipeline change)
- Primary: remove a recurring tax on every schema change

**Action:** skip. Cost of a codegen layer > value at our schema churn rate. Acknowledge it's duplication we accept.

---

### [C07] Worker route matching via if-ladder

**Verified.** `worker/src/index.ts:188,196` + `worker-gmail/src/index.ts:140,164,171`:

```ts
if (pathname === "/mail/sync" && request.method === "POST") { … }
if (pathname === "/api/mail/list" && request.method === "GET") { … }
if (pathname === "/api/mail/trash" && request.method === "POST") { … }
```

Works for 5 endpoints. At 10+ endpoints becomes bug-prone (forget a method guard, forget a CORS preflight branch, forget return after match). No path params beyond one (prices/:symbol regex match in portal-api).

- LoC impact: ~30 LoC added for a router lib
- Risk: **L** (isolated change per Worker)
- Primary: type-safe path params, automatic method mismatch responses

**Action:** defer. When the next endpoint lands, consider [itty-router](https://itty.dev/itty-router/) or Hono if we're doing more than a trivial handler.

---

## 🟢 Cosmetic (flag only, don't fix)

- **Worker CORS asymmetry** — portal-api has full `Access-Control-*` set; worker-gmail just `Cache-Control: no-store`. Same-origin now, both are no-ops. No bug, just inconsistency.
- **`_add_*` helpers' dict-mutation style** — see C04; also hard-to-test in isolation, but refactoring without a use case is premature abstraction.
- **CI didn't use `continue-on-error: true`** on the Deploy Worker step — the failing step blocked everything after it (including post-steps) for days. Now moot (we deleted the step in #140).

---

## Summary

| ID | Finding | Severity | Action |
|---|---|---|---|
| **C01** | Env var URL conventions inconsistent (base vs. full URL) | **High** | **Execute** — unify, delete regex |
| **C02** | `WORKER_BASE` regex-strip brittle | High | Subsumed by C01 |
| **C03** | Silent-drop pattern for missing prices on valuable positions | Medium | Promote warning → fatal for large holdings |
| **C04** | `compute_daily_allocation` 6-positional signature | Medium | Defer (dataclass when next data source lands) |
| **C05** | `timemachine.py` 602 LoC dumping ground | Low | Defer (split on next real change) |
| **C06** | Python/TS schema duplication | Low | Accept |
| **C07** | Worker if-ladder routing | Low | Defer (router lib when endpoint count grows) |

**The only "do now" is C01 + C02** — today's migration has fresh memory of how this particular convention conflict burns 20 minutes of debugging. Small PR, low risk, eliminates a recurring footgun. Everything else is defensible as-is.

## What this audit ruled out

- **`compute.ts` (262 LoC)** is fine. Brush-drag derived state is intentionally centralized here; splitting would just scatter it.
- **`use-bundle.ts` (162 LoC)** has several `useState` + `useEffect` pairs but they encode genuinely independent concerns (fetch state, range index, per-section errors). React Compiler memoizes; no `useMemo` needed.
- **`precompute.py` (288 LoC)** has a dozen `_precompute_*` helpers but each is cohesive — splitting would be cosmetic.
- **`allocation.py` (368 LoC)** is big but every helper has a clear job; the weight comes from *breadth* of data sources, not confused responsibilities.
- **Env var substitution in CI** (`ci.yml:82`) had a stale default (`portal-worker.guoyuer.workers.dev`) — not a design smell, just docs to update in the normal course.
