# Code Quality Review — 2026-04

Findings from a targeted audit of the frontend + worker. Scoped to real defects, not stylistic preferences. Previous review's leftovers are tracked in `refactor-candidates.md`; this file contains net-new findings.

---

## 1. Worker isn't really "pure passthrough"

**Files:** `worker/src/index.ts`, `CLAUDE.md:49`

CLAUDE.md claims the worker is a "pure passthrough (SELECT → JSON)". The code disagrees:

- `worker/src/index.ts:43-47` — `/econ` computes `snapshot = last value per series` (derived data, not a SELECT result).
- `worker/src/index.ts:137-140` — `/timeline` calls `JSON.parse(r.sparkline)` because the column is stored as a JSON string in D1.
- `worker/src/index.ts:118-121, 141` — indicators are collapsed into a flat object and spread into `market` alongside the `indices` array, so `market` ends up as a mixed bag of an array + scalar keys.

**Fix options:**
1. Update CLAUDE.md to describe the actual transforms (cheap, honest).
2. Move the last-value snapshot into a D1 view (`v_econ_snapshot`) and store `sparkline` as a proper array at pipeline time.

---

## 2. Worker type safety: casts all the way down

**File:** `worker/src/index.ts:38, 50, 120, 126, 137`

Every `.results` read uses an `as { ... }[]` / `as Record<string, unknown>[]` cast with no runtime validation. Only `/timeline` gets Zod-validated on the client (`src/lib/use-bundle.ts:73`). `/prices/:symbol` and `/econ` have no schema check at either end — if a D1 view drifts, failure is silent.

**Fix:** Either
- Add Zod schemas on the worker side (shared with `src/lib/schema.ts` via a `types/` workspace), or
- At minimum, run `TimelineDataSchema` / a new `PricesResponseSchema` / `EconResponseSchema` at the fetch boundary for all three endpoints.

---

## 3. Fidelity date parsing is string-slice with zero validation

**File:** `src/lib/compute.ts:115-127`

```ts
export function fidelityDateToIso(runDate: string): string {
  return `${runDate.slice(6, 10)}-${runDate.slice(0, 2)}-${runDate.slice(3, 5)}`;
}
```

Assumes exactly `MM/DD/YYYY`. An empty string, an ISO input, or a one-digit month (`M/D/YYYY`) silently produces a garbage ISO string. Downstream, `new Date(...)` becomes `Invalid Date`, `.getTime()` returns `NaN`, and `computeCrossCheck`'s distance filter (`dist <= MATCH_WINDOW_MS`) drops to `false` — reconciliation **silently under-matches** with no surfaced error.

**Fix:** Validate format before slicing:

```ts
const FIDELITY_DATE_RE = /^(\d{2})\/(\d{2})\/(\d{4})$/;
export function fidelityDateToIso(runDate: string): string {
  const m = FIDELITY_DATE_RE.exec(runDate);
  if (!m) throw new Error(`Invalid Fidelity date: ${runDate}`);
  return `${m[3]}-${m[1]}-${m[2]}`;
}
```

Add a test for the reject path — the existing `compute.test.ts` only covers happy inputs.

---

## 4. `computeCashflow` hardcodes a `"401"` substring match

**File:** `src/lib/compute.ts:105`

```ts
const k401 = incomeItems.find((i) => i.category.toLowerCase().includes("401"))?.amount ?? 0;
```

The `takehomeSavingsRate` metric shown in the UI depends on whether the Qianji category name happens to contain the substring `"401"`. Rename the category in Qianji (e.g. `"Retirement — 401(k) Match"` → `"Employer Retirement Match"`) and the metric silently breaks.

**Fix:** Promote the mapping to config. Either
- Add a `retirement_categories: string[]` field to `pipeline/config.json` and thread it through to the timeline bundle, or
- Tag these transactions at pipeline time (emit `is_retirement: boolean` on the row) so the frontend does no string sniffing.

---

## 5. Worker `/timeline` is fail-closed on 8 parallel queries

**File:** `worker/src/index.ts:99-109`

Eight `env.DB.prepare(...).all()` calls fan out inside a single `Promise.all`. Any one failure → whole `/timeline` returns 502, dashboard dark.

Several of these views are not critical to the primary experience — e.g. `v_market_indicators` and `v_market_indices` feed the market context panel. If the market sync job fails but daily/fidelity data is fresh, the user still wants to see their net worth.

**Fix:** Use `Promise.allSettled` and degrade the optional views to `null`. The Zod schema already models `market` fields as nullable (`src/lib/schema.ts`); make the worker actually exercise that.

---

## 6. CORS falls back to production origin on unknown callers

**File:** `worker/src/index.ts:10`

```ts
const allowed = origin && ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
```

Unknown origins get told `Access-Control-Allow-Origin: https://portal.guoyuer.com`. Browser will block the cross-origin request (so no security hole), but the response is misleading. If an allowed domain ever changes, debugging will be needlessly confusing.

**Fix:** Omit the `Access-Control-Allow-Origin` header entirely for non-whitelisted origins, or return 403 on `OPTIONS` preflight from unknown origins.

---

## 7. Category palette + targets live in two places

**File:** `src/lib/compute.ts:33-38`, mirrored from `pipeline/generate_asset_snapshot/_CATEGORIES`

The comment on line 30 even says "mirror server `_CATEGORIES`" — explicit acknowledgement of a dual source of truth. Target weights (55/15/3/27) drift is a classic "works locally, wrong in prod" bug.

**Fix:** Store category metadata (`name`, `key`, `target_pct`, `color`) in D1 as a `categories` table populated by the pipeline, expose via `/timeline`. Frontend reads the canonical list instead of redeclaring it.

The Okabe-Ito palette itself is fine; the problem is the *targets* co-existing in two codebases.

---

## Non-findings (retracted from initial draft)

- ~~"`use-bundle.ts` needs `useMemo`"~~ — `next.config.ts:6` has `reactCompiler: true`. `dateIndex`/`tickerIndex` (data-derived) are auto-memoized. The per-brush-drag recomputations depend on `startDate`/`snapshotDate` which *do* change on drag; `useMemo` wouldn't help there either. If drag becomes slow, the real lever is `computeCrossCheck`'s O(deposits × transfers) scan — pre-bucket transfers by month to get O(n + m).

---

## Priority

| # | Severity | Why |
|---|----------|-----|
| 3 | P0 | Silent reconciliation errors on malformed dates |
| 4 | P0 | User-facing metric depends on fragile string match |
| 2 | P1 | Schema contract break surfaces far from cause |
| 5 | P1 | Whole-dashboard outage from one view failing |
| 1 | P2 | Documentation vs. code drift |
| 6 | P2 | Misleading CORS response |
| 7 | P2 | Latent dual-source-of-truth bug |
