# Investment Activity — Design Spec

**Date:** 2026-04-20
**Status:** Draft
**Owner:** Yuer Guo

Extend the existing Fidelity Activity section into a unified **Investment Activity** surface covering Fidelity + 401k (Empower) + Robinhood. Rename, refactor the matching pipeline, and extend the cross-check to reconcile deposits/contributions from all three brokers against Qianji.

## Goals

- Show a single, unified activity view covering every source of investment buys, sells, dividends, and contributions.
- Extend the cross-check reconciliation badge to cover all three brokers (not just Fidelity).
- Preserve source provenance in the UI (per-row source badges) without duplicating tables.
- Keep raw per-source txn arrays in the timeline response so other features (ticker dialog, etc.) can still query source-specific data.
- No backcompat shims — full migration of tests, type signatures, section IDs.

## Non-goals

- No staging env / canary infrastructure (overhead not justified; L1/L2 regression gate + local-before-prod is sufficient).
- No per-account matching (Fidelity `Z29133576` vs `Z29276228`); `accountTo.startsWith("fidelity")` is adequate.
- No display of Robinhood `action_kind='other'` (account fees, RTP receipts, etc.) — filtered.
- No aggregation/merging at the D1 pipeline layer. Normalization happens in the frontend compute.ts.
- **No UI cross-check for 401k contributions.** The pipeline already reconciles QFX vs Qianji at ingest time (`ContributionReconcileError` on per-date $1 tolerance mismatch) — adding a UI cross-check layer would be ~100% tautological (Qianji-fallback rows match by construction; QFX rows are guaranteed to match Qianji on overlap dates). UI cross-check covers only Fidelity + Robinhood where no pipeline-level reconciliation exists.

## Data flow

```
Python ETL                D1                          Zod                       Compute / UI
────────────              ──────────────              ──────────                ──────────────
fidelity_transactions  →  v_fidelity_txns          → FidelityTxn          ─┐
robinhood_transactions →  v_robinhood_txns  (new)  → RobinhoodTxn (new)   ─┤→ normalizeInvestmentTxns() → InvestmentTxn[] → computeActivity / computeCrossCheck → UI
empower_contributions  →  v_empower_contribs(new)  → EmpowerContribution  ─┘                                                                         ↑
qianji_transactions    →  v_qianji_txns            → QianjiTxn                                                                         ─────────────┘
```

- `FidelityTxn`, `RobinhoodTxn`, `EmpowerContribution` all cross the D1/Worker boundary as first-class Zod types.
- `InvestmentTxn` is a **compute-layer internal** type only; it does NOT cross the D1/Worker boundary and does NOT appear in Zod.
- `normalizeInvestmentTxns` is the ONLY function that converts per-source types into `InvestmentTxn[]`. Downstream (`computeActivity`, `computeCrossCheck`) consumes the normalized array.

## Decisions

### Scope
- Sources: Fidelity + 401k (Empower) + Robinhood.
- Robinhood `action_kind='other'` filtered.
- 401k contributions are represented as `actionType='contribution'` but aggregated into the **Buys** table for display (via EQUIVALENT_GROUPS).

### Cross-check semantics
- Extended to Fidelity + Robinhood (not 401k — see Non-goals). Badge shows aggregate `matchedCount/totalCount`.
- Per-source matching runs independently (no cross-source candidate pooling).
- Matching predicates:
  | Source | Deposit source | Qianji candidate predicate |
  |---|---|---|
  | Fidelity | `fidelityTxns where actionType='deposit'` | `type='transfer'` OR `type='income' AND accountTo.startsWith('fidelity')` (case-insensitive) |
  | Robinhood | `robinhoodTxns where actionKind='deposit'` | `type='transfer'` OR `type='income' AND accountTo.startsWith('robinhood')` |
- `MATCH_WINDOW_MS=7d`, Qianji floor (`earliestQianji − 7d`), dust filter (`< $1`), earliest-in-window matching algorithm — all reused.

### UI
- Section ID: `fidelity-activity` → `investment-activity`
- Section title: "Fidelity Activity" → "Investment Activity"
- Sidebar label: "Fidelity" → "Investment"
- Layout: **unified tables with per-row source badges** (Layout Option B from brainstorm).
  - Buys / Sells / Dividends tables aggregate across all three sources via EQUIVALENT_GROUPS.
  - Each row carries `sources: Array<"fidelity"|"robinhood"|"401k">` (deduped); rendered as colored badges next to the ticker name.
- Cross-check badge:
  - Aggregate single number displayed inline (e.g., `✓ 227/227 deposits reconciled`).
  - Hover tooltip shows per-source breakdown.
  - On failure (red X), badge becomes clickable → expands an inline `UnmatchedPanel` below the section header listing each unmatched item with date, amount, source, and (for 401k) which fund(s).

## Type contract

### New Zod types (generated from Python TypedDicts)

```ts
interface RobinhoodTxn {
  txnDate: string;        // ISO YYYY-MM-DD
  action: string;
  actionKind: string;     // "buy" | "sell" | "dividend" | "deposit" | "other"
  ticker: string;
  quantity: number;
  amountUsd: number;
  rawDescription: string;
}

interface EmpowerContribution {
  date: string;
  amount: number;
  ticker: string;         // "401k sp500" | "401k tech" | "401k ex-us"
  cusip: string;
}
```

Both added to `timeline.ts` response schema as **required** arrays (no `.default([])`):

```ts
robinhoodTxns: z.array(RobinhoodTxnSchema),
empowerContributions: z.array(EmpowerContributionSchema),
```

### Compute-layer internal type

```ts
interface InvestmentTxn {
  source: "fidelity" | "robinhood" | "401k";
  date: string;
  ticker: string;
  actionType: "buy" | "sell" | "dividend" | "reinvestment" | "deposit" | "contribution";
  amount: number;
}
```

### Updated `CrossCheck` shape

```ts
interface CrossCheck {
  matchedCount: number;    // aggregate across fidelity + robinhood
  totalCount: number;
  ok: boolean;
  perSource: {
    fidelity:  SourceCrossCheck;
    robinhood: SourceCrossCheck;
  };
  allUnmatched: UnmatchedItem[];     // flat list for drawer
}

interface SourceCrossCheck {
  matched: number;
  total: number;
  unmatched: UnmatchedItem[];
}

interface UnmatchedItem {
  source: "fidelity" | "robinhood";
  date: string;
  amount: number;
}
```

**Invariant (tested):** `matchedCount === perSource.fidelity.matched + perSource.robinhood.matched`; `totalCount === perSource.fidelity.total + perSource.robinhood.total`.

Old fields (`fidelityTotal`, `matchedTotal`, `unmatchedTotal`) are removed — no backcompat aliases.

### `ApiTicker` row shape

```ts
interface ApiTicker {
  ticker: string;
  count: number;
  total: number;
  isGroup: boolean;
  groupKey?: string;
  sources: Array<"fidelity" | "robinhood" | "401k">;  // new; deduped
}
```

## Compute layer changes

### New function

```ts
export function normalizeInvestmentTxns(
  fidelity: FidelityTxn[],
  robinhood: RobinhoodTxn[],
  empower: EmpowerContribution[],
): InvestmentTxn[]
```

- Fidelity: 1:1 map, preserve `actionType` values present today (`buy`/`sell`/`dividend`/`reinvestment`/`deposit`).
- Robinhood: filter `actionKind='other'`; map remaining 1:1.
- Empower: all rows → `actionType='contribution'`. Do NOT aggregate here; `computeCrossCheck` handles per-paycheck aggregation internally.

### Refactored signatures (no backcompat wrappers)

```ts
computeActivity(investmentTxns: InvestmentTxn[], start: string, end: string): ActivityResponse
computeGroupedActivity(investmentTxns: InvestmentTxn[], start: string, end: string): GroupedActivityResponse
computeCrossCheck(investmentTxns: InvestmentTxn[], qianjiTxns: QianjiTxn[], start: string, end: string): CrossCheck
```

- `computeActivity`: accumulates buys / sells / dividends; `reinvestment` contributes to both dividends and buys (existing behavior); `contribution` contributes to buys only. Tracks `sources` Set per row.
- `computeCrossCheck`: filters `InvestmentTxn[]` into two deposit pools (fidelity + robinhood), runs earliest-in-window matching per source with source-specific Qianji candidate predicates, returns combined `CrossCheck`. 401k contributions in the array are ignored by this function (not part of UI cross-check scope).

### `use-bundle.ts`

- Add `robinhoodTxns`, `empowerContributions` to the bundle.
- Compute `investmentTxns = normalizeInvestmentTxns(fidelityTxns, robinhoodTxns, empowerContributions)` once (React Compiler memoizes).
- Pass `investmentTxns` into `computeActivity`, `computeGroupedActivity`, `computeCrossCheck`.
- Raw per-source arrays remain exposed for other consumers (ticker dialog, chart components).

## UI components

### `finance/page.tsx`

- `ErrorBoundary` label: `Investment Activity`.
- `<section id="investment-activity">` with `SectionHeader>Investment Activity</SectionHeader>`.
- Badge:
  - Span (hover tooltip) for success state.
  - Button (expandable) for failure state: toggles `expanded` local state; on expand, renders `<UnmatchedPanel items={crossCheck.allUnmatched} />` below the header.
- `<ActivityContent>` props: drop `fidelityTxns`-only prop, add `investmentTxns`. Drop-down chain to `TickerTable` now carries `sources` on each row.

### New component: `UnmatchedPanel`

- ~25 lines. Renders a small list grouped by source:
  ```
  Fidelity (N):
    2024-10-01  $500.00
  Robinhood (M):
    2024-11-20  $500.00
  ```
- Uses existing tailwind / color tokens; no new design system pieces.

### New component: `SourceBadge`

- ~10 lines. Renders a colored pill: `FID` (Fidelity, Okabe-Ito blue), `RH` (Robinhood, Okabe-Ito green), `401k` (Okabe-Ito orange).
- Accessibility: pairs color with letter text (existing project rule: never rely on color alone).

### `ticker-table.tsx`

- In each row, render `row.sources.map((s) => <SourceBadge source={s} />)` inline next to ticker name.
- Multiple badges stack horizontally when a ticker has multiple sources (e.g., S&P 500 grouped row: `[FID] [401k]`).

### `sidebar.tsx`

- Entry updated: `{ label: "Investment", hash: "#investment-activity" }`.

## Pipeline changes

### `etl/types.py`

Add TypedDicts:

```python
class RobinhoodTxn(TypedDict):
    txn_date: str
    action: str
    action_kind: str
    ticker: str
    quantity: float
    amount_usd: float
    raw_description: str

class EmpowerContribution(TypedDict):
    date: str
    amount: float
    ticker: str
    cusip: str
```

### `etl/db.py`

Add two views (written in schema-generator module; SQL auto-regenerated via `gen_schema_sql.py`):

```sql
CREATE VIEW v_robinhood_txns AS
SELECT txn_date AS txnDate, action, action_kind AS actionKind, ticker,
       quantity, amount_usd AS amountUsd, raw_description AS rawDescription
FROM robinhood_transactions ORDER BY txn_date;

CREATE VIEW v_empower_contributions AS
SELECT date, amount, ticker, cusip
FROM empower_contributions ORDER BY date;
```

No new tables — `robinhood_transactions` and `empower_contributions` are already populated by existing ingest.

### `tools/gen_zod.py`

Add `ViewSpec` entries for both new types; regenerate `src/lib/schemas/_generated.ts`. Pytest parity check gates drift.

## Worker changes

`worker/src/index.ts`:

```ts
settled(env.DB.prepare("SELECT * FROM v_robinhood_txns").all()),
settled(env.DB.prepare("SELECT * FROM v_empower_contributions").all()),
```

Added to the `Promise.all` block alongside existing SELECTs. Response object gets `robinhoodTxns` and `empowerContributions` fields.

`settled()` wrapper preserves fail-open behavior: if the view is missing (e.g., D1 not yet synced), that section degrades to `null` and surfaces in `errors` — but since we remove `.default([])` on the Zod, the frontend will hard-fail instead of silently showing empty. This is intentional: forces the deploy checklist to be followed.

## Regression防护 (10 items)

| # | Defense | Layer |
|---|---|---|
| 1 | All existing `computeActivity` / `computeCrossCheck` / `computeGroupedActivity` call sites migrated to `InvestmentTxn[]` (no shim wraps `[fid, [], []]`) | unit tests |
| 2 | `e2e/mock-api.ts` fixture includes ≥1 Robinhood buy+sell, ≥1 401k contribution, ≥1 unmatched Robinhood deposit | E2E |
| 3 | New factories: `mkInvestmentTxn({source, actionType, ...})`, `mkRobinhoodTxn`, `mkEmpowerContribution` | test support |
| 4 | Test case: `computeCrossCheck` ignores `source="401k"` contributions even when in the InvestmentTxn[] input (invariant: contribution entries contribute neither to matched nor total) | unit tests |
| 5 | PR description explicitly lists expected first-render unmatched items ("baseline: ~A Robinhood unmatched, ~B 401k unmatched from pre-Qianji period") | docs |
| 6 | Merge-blocking checklist: prod `wrangler d1 execute --remote --file=schema.sql` → `sync_to_d1.py` → `wrangler deploy` (Worker) → merge (Pages auto-deploys) | ops |
| 7 | Pipeline L1/L2 regression gate re-run before merge; L1 baselines must not drift (views don't change outputs) | data |
| 8 | Unit test: `CrossCheck.matchedCount === Σ perSource[*].matched` and same for total | data |
| 9 | Fidelity baseline anchor: `perSource.fidelity` must be `101/101 ✓` post-merge. If not, rollback immediately | data |
| 10 | Rollback plan in PR description: `git revert <merge_commit>` for frontend; `cd worker && git checkout HEAD~1 -- src/ && npx wrangler deploy` for Worker; D1 views are additive, no rollback needed | ops |

## Rollout plan (strict order, no softening)

Pre-merge:
1. Local: `build_timemachine_db.py` + `sync_to_d1.py --local` → probe `http://localhost:8787/timeline` JSON for `robinhoodTxns` + `empowerContributions` fields.
2. Local: full vitest + Playwright + Pipeline pytest green.
3. Prod D1 schema: `cd worker && npx wrangler d1 execute portal-db --remote --file=schema.sql` (adds new views; idempotent via IF NOT EXISTS on tables, DROP/CREATE on views).
4. Prod D1 data: `cd pipeline && .venv/Scripts/python.exe scripts/sync_to_d1.py` (diff mode sufficient — new data flows via existing pipeline path).
5. Prod Worker: `cd worker && npx wrangler deploy`.
6. Verify: user refreshes https://portal.guoyuer.com/finance, opens DevTools Network → `/timeline` response has `robinhoodTxns` and `empowerContributions` arrays populated.

Merge:
7. Merge PR → Pages auto-deploys frontend.
8. User refreshes prod: Investment Activity section renders with merged data; hover badge for tooltip; click if red X to see drawer.

Post-merge:
9. Compare `perSource.fidelity` to pre-PR baseline (`101/101`). Mismatch → rollback step 10.

Rollback (if needed):
10. `git revert <merge_commit>` + push; `cd worker && git checkout HEAD~1 -- src/ && npx wrangler deploy`. D1 views can stay (harmless).

## Open questions / follow-ups

- If first render surfaces many 401k unmatched from 2023-early-2024 (pre-Qianji floor), consider whether the floor should also apply per-source (401k-specific earliestQianji). Decide after seeing real data.
- A future "ticker details dialog" cross-source view (show buys of NVDA across Fidelity + Robinhood + 401k) would build on `InvestmentTxn`. Explicitly out of scope for this PR.
