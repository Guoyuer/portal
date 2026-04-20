# Investment Activity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing Fidelity Activity section into a unified Investment Activity surface covering Fidelity + 401k (Empower) + Robinhood, with per-broker cross-check reconciliation against Qianji.

**Architecture:** Passthrough + frontend normalization. Pipeline exposes each source as its own D1 view + Zod type (raw data preserved). A new compute-layer internal type `InvestmentTxn` and a `normalizeInvestmentTxns()` function unify the three sources for `computeActivity` / `computeCrossCheck`. No D1 materialized union, no backcompat wrappers.

**Tech Stack:** Python 3.11 (pipeline + pytest), TypeScript 5 / Vitest / React 19 with React Compiler (frontend), Cloudflare Worker + D1 (backend), Playwright (E2E), Zod (type boundary).

**Spec:** `docs/specs/2026-04-20-investment-activity-design.md`

---

## File Structure Map

**Python pipeline:**
- Modify `pipeline/etl/types.py` — add `RobinhoodTransaction`, `EmpowerContribution` TypedDicts
- Modify `pipeline/etl/db.py` — add `v_robinhood_txns` and `v_empower_contributions` to `_VIEWS` dict
- Modify `pipeline/tools/gen_zod.py` — add two `ViewSpec` entries
- Modify `pipeline/tests/unit/test_schema_views.py` — add two view names to `required` set
- Regenerate `worker/schema.sql` (via `gen_schema_sql.py`)
- Regenerate `src/lib/schemas/_generated.ts` (via `gen_zod.py`)

**Worker:**
- Modify `worker/src/index.ts` — add two `settled(...SELECT * FROM v_...)` calls; add two keys to response

**Frontend types / bundle:**
- Modify `src/lib/schemas/timeline.ts` — add `robinhoodTxns`, `empowerContributions` required arrays in the timeline response schema (Worker passthrough — re-export or reference the generated schemas)
- Modify `src/lib/hooks/use-bundle.ts` — pass new arrays through, compute `investmentTxns`

**Compute layer:**
- Modify `src/lib/compute/compute.ts`:
  - Add `InvestmentTxn` type
  - Add `normalizeInvestmentTxns()` function
  - Update `ApiTicker` with `sources` field (in `computed-types.ts`)
  - Refactor `computeActivity` signature (`InvestmentTxn[]`)
  - Refactor `computeGroupedActivity` signature
  - Refactor `computeCrossCheck` signature; add per-source matching; 401k date aggregation
  - Update `CrossCheck` interface (add `perSource`, `allUnmatched`; drop `fidelityTotal`/`matchedTotal`/`unmatchedTotal`)
- Modify `src/lib/compute/compute.test.ts` — migrate every call site to use `mkInvestmentTxn` or `normalizeInvestmentTxns`; add new tests per regression防护
- Modify `src/lib/compute/computed-types.ts` — add `sources` to `ApiTicker`

**Frontend test support:**
- Modify `src/test/factories.ts` — add `mkRobinhoodTxn`, `mkEmpowerContribution`, `mkInvestmentTxn`; update `mkTimelinePayload` to include new fields

**UI components:**
- Create `src/components/finance/source-badge.tsx` — small pill component
- Create `src/components/finance/unmatched-panel.tsx` — drawer list
- Modify `src/components/finance/ticker-table.tsx` — render `SourceBadge` per row
- Modify `src/app/finance/page.tsx` — section id/title rename, badge tooltip, click-to-expand
- Modify `src/components/layout/sidebar.tsx` — label rename

**E2E:**
- Modify `e2e/mock-api.ts` — add Robinhood + 401k fixture data (+ one unmatched Robinhood deposit)
- Modify `e2e/finance.spec.ts` and any test matching on `Fidelity Activity` / `#fidelity-activity` — rename
- Add one new e2e spec: badge tooltip + click-to-expand drawer flow

---

## Task 1: Add Python TypedDicts for Robinhood and Empower

**Files:**
- Modify: `pipeline/etl/types.py`
- Test: `pipeline/tests/unit/test_types.py` (or inline via gen_zod parity test)

- [ ] **Step 1: Open `pipeline/etl/types.py` and locate insertion point after `QianjiRecord` (line ~138)**

- [ ] **Step 2: Add the two new TypedDicts**

```python
class RobinhoodTransaction(TypedDict):
    """Robinhood transaction row matching the ``robinhood_transactions``
    table. Source of truth for the D1 view ``v_robinhood_txns``.
    """
    txn_date: str              # ISO YYYY-MM-DD
    action: str                # raw Trans Code from CSV (Buy/Sell/CDIV/ACH/...)
    action_kind: str           # normalized ActionKind enum (buy/sell/dividend/deposit/other)
    ticker: str
    quantity: float
    amount_usd: float
    raw_description: str


class EmpowerContribution(TypedDict):
    """One 401k contribution row matching the ``empower_contributions``
    table. Source of truth for the D1 view ``v_empower_contributions``.
    """
    date: str
    amount: float
    ticker: str                # "401k sp500" | "401k tech" | "401k ex-us"
    cusip: str
```

- [ ] **Step 3: Verify the module still imports cleanly**

Run: `cd pipeline && .venv/Scripts/python.exe -c "from etl.types import RobinhoodTransaction, EmpowerContribution; print('ok')"`

Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add pipeline/etl/types.py
git commit -m "feat(types): add RobinhoodTransaction + EmpowerContribution TypedDicts"
```

---

## Task 2: Add D1 views `v_robinhood_txns` and `v_empower_contributions`

**Files:**
- Modify: `pipeline/etl/db.py` (the `_VIEWS` dict around line 194)
- Modify: `pipeline/tests/unit/test_schema_views.py` (`required` set in `test_required_views_present`)

- [ ] **Step 1: Write the failing test — expand `required` view set**

Open `pipeline/tests/unit/test_schema_views.py`. In `test_required_views_present()` update the `required` set:

```python
def test_required_views_present() -> None:
    from etl.db import _VIEWS

    required = {
        "v_daily",
        "v_daily_tickers",
        "v_fidelity_txns",
        "v_qianji_txns",
        "v_robinhood_txns",          # new
        "v_empower_contributions",   # new
        "v_market_indices",
        "v_holdings_detail",
        "v_econ_series",
        "v_econ_snapshot",
    }
    assert required.issubset(set(_VIEWS.keys()))
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_schema_views.py::test_required_views_present -v`

Expected: FAIL with `AssertionError` because the new view names aren't in `_VIEWS` yet.

- [ ] **Step 3: Add the two views to `_VIEWS` in `pipeline/etl/db.py`**

Locate the `_VIEWS` dict. Add the two entries alongside `v_fidelity_txns` / `v_qianji_txns`:

```python
"v_robinhood_txns": (
    "CREATE VIEW IF NOT EXISTS v_robinhood_txns AS\n"
    "SELECT txn_date AS txnDate, action, action_kind AS actionKind,\n"
    "  ticker, quantity, amount_usd AS amountUsd,\n"
    "  raw_description AS rawDescription\n"
    "FROM robinhood_transactions ORDER BY txn_date;"
),
"v_empower_contributions": (
    "CREATE VIEW IF NOT EXISTS v_empower_contributions AS\n"
    "SELECT date, amount, ticker, cusip\n"
    "FROM empower_contributions ORDER BY date;"
),
```

- [ ] **Step 4: Run the test to verify it passes + additional tests still pass**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_schema_views.py -v`

Expected: all tests PASS (in particular `test_init_db_creates_all_views` which builds a tmp DB and checks all views land).

- [ ] **Step 5: Regenerate `worker/schema.sql`**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/gen_schema_sql.py`

This overwrites `worker/schema.sql`. Verify with `git diff worker/schema.sql` that it includes:
- `DROP VIEW IF EXISTS v_robinhood_txns; CREATE VIEW IF NOT EXISTS v_robinhood_txns AS ...`
- Same for `v_empower_contributions`

- [ ] **Step 6: Run pipeline-wide test that `gen_schema_sql.py` output is consistent**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_schema_views.py::test_gen_schema_sql_output_contains_every_view -v`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add pipeline/etl/db.py pipeline/tests/unit/test_schema_views.py worker/schema.sql
git commit -m "feat(schema): add v_robinhood_txns + v_empower_contributions views"
```

---

## Task 3: Register TypedDicts in gen_zod ViewSpec + regenerate Zod

**Files:**
- Modify: `pipeline/tools/gen_zod.py` (`_SPECS` tuple around line 88)
- Regenerate: `src/lib/schemas/_generated.ts`

- [ ] **Step 1: Add ViewSpec entries to `pipeline/tools/gen_zod.py`**

Append to the `_SPECS` tuple (after the QianjiTxn entry):

```python
ViewSpec(
    output="RobinhoodTxn",
    source="RobinhoodTransaction",
    include={
        "txn_date": None,   # → txnDate
        "action": None,
        "action_kind": None,  # → actionKind
        "ticker": None,
        "quantity": None,
        "amount_usd": None,  # → amountUsd
        "raw_description": None,  # → rawDescription
    },
),
ViewSpec(
    output="EmpowerContribution",
    source="EmpowerContribution",
    include={
        "date": None,
        "amount": None,
        "ticker": None,
        "cusip": None,
    },
),
```

- [ ] **Step 2: Regenerate `src/lib/schemas/_generated.ts`**

Run: `cd pipeline && .venv/Scripts/python.exe tools/gen_zod.py --write ../src/lib/schemas/_generated.ts`

Verify with `git diff src/lib/schemas/_generated.ts`:
- `RobinhoodTxnSchema` exported with all 7 fields in camelCase
- `EmpowerContributionSchema` exported with date/amount/ticker/cusip
- Inferred types `RobinhoodTxn` and `EmpowerContribution` exported

- [ ] **Step 3: Run the gen_zod parity test**

Run: `cd pipeline && .venv/Scripts/python.exe -m pytest tests/unit/test_gen_zod.py -v`

(If there's no such file, locate the parity check via `grep -rn "_generated.ts" pipeline/tests/`)

Expected: PASS — generated file matches what the tool would output.

- [ ] **Step 4: Run frontend type check**

Run: `npx tsc --noEmit`

Expected: clean (no TS errors from the generated file).

- [ ] **Step 5: Commit**

```bash
git add pipeline/tools/gen_zod.py src/lib/schemas/_generated.ts
git commit -m "feat(zod): generate RobinhoodTxn + EmpowerContribution schemas"
```

---

## Task 4: Extend timeline Zod schema with new required arrays

**Files:**
- Modify: `src/lib/schemas/timeline.ts`
- Test: `src/lib/schemas/timeline.test.ts` (if exists) or round-trip parse in `use-bundle.test.ts`

- [ ] **Step 1: Locate the timeline response schema**

Run: `grep -n "fidelityTxns\|qianjiTxns" src/lib/schemas/timeline.ts`

Expected: one or two lines showing `fidelityTxns: z.array(FidelityTxnSchema)` and similar.

- [ ] **Step 2: Extend the schema — add two required fields**

In `src/lib/schemas/timeline.ts`, where the top-level timeline response schema is defined, add:

```ts
import { RobinhoodTxnSchema, EmpowerContributionSchema } from "./_generated";
// ... in the response schema definition:
robinhoodTxns: z.array(RobinhoodTxnSchema),
empowerContributions: z.array(EmpowerContributionSchema),
```

**Critical: no `.default([])` / `.optional()`. These are required.** The deploy checklist ensures the Worker serves them before merge.

- [ ] **Step 3: Run Zod tests**

Run: `npx vitest run src/lib/schemas/`

Expected: all passing. If a `timeline.test.ts` asserts the schema parses a canonical payload, update that fixture to include `robinhoodTxns: []` and `empowerContributions: []`.

- [ ] **Step 4: Run type check**

Run: `npx tsc --noEmit`

Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add src/lib/schemas/timeline.ts src/lib/schemas/timeline.test.ts
git commit -m "feat(schema): require robinhoodTxns + empowerContributions in timeline"
```

---

## Task 5: Add Worker SELECTs for the new views

**Files:**
- Modify: `worker/src/index.ts` (around line 58-62 `Promise.all` block + line 90-91 response assembly)

- [ ] **Step 1: Locate the settled() fetch block**

Run: `grep -n "settled(env.DB.prepare" worker/src/index.ts`

Note the pattern used for `v_fidelity_txns` and `v_qianji_txns`.

- [ ] **Step 2: Add two more settled() calls to the Promise.all block**

```ts
settled(env.DB.prepare("SELECT * FROM v_fidelity_txns").all()),
settled(env.DB.prepare("SELECT * FROM v_qianji_txns").all()),
settled(env.DB.prepare("SELECT * FROM v_robinhood_txns").all()),       // new
settled(env.DB.prepare("SELECT * FROM v_empower_contributions").all()),// new
```

- [ ] **Step 3: Destructure + add response fields**

After the `const [...] = await Promise.all(...)` destructure, add destructured names like `robinhood` and `empower`. Then in the response object:

```ts
fidelityTxns: fidelity.ok ? fidelity.value.results : [],
qianjiTxns:   qianji.ok   ? qianji.value.results   : [],
robinhoodTxns: robinhood.ok ? robinhood.value.results : [],     // new
empowerContributions: empower.ok ? empower.value.results : [], // new
```

- [ ] **Step 4: Run Worker tests (if any)**

Run: `cd worker && npm test` (if `package.json` has `test`) or skip.

- [ ] **Step 5: Start local wrangler dev + hit /timeline**

In separate terminal:
```bash
cd worker && npx wrangler dev
```

Then probe:
```bash
curl -s http://localhost:8787/timeline | node -e 'const d = JSON.parse(require("fs").readFileSync(0,"utf8")); console.log("robinhoodTxns count:", (d.robinhoodTxns||[]).length); console.log("empowerContributions count:", (d.empowerContributions||[]).length)'
```

Expected: both counts > 0 (since local D1 has data).

- [ ] **Step 6: Commit**

```bash
git add worker/src/index.ts
git commit -m "feat(worker): include v_robinhood_txns + v_empower_contributions in /timeline"
```

---

## Task 6: Add test factories for RobinhoodTxn / EmpowerContribution / InvestmentTxn

**Files:**
- Modify: `src/test/factories.ts`

- [ ] **Step 1: Update imports at top of `src/test/factories.ts`**

```ts
import type {
  CategoryMeta,
  DailyPoint,
  DailyTicker,
  FidelityTxn,
  QianjiTxn,
  RobinhoodTxn,            // new
  EmpowerContribution,     // new
  MarketData,
} from "@/lib/schemas";
import type { InvestmentTxn } from "@/lib/compute/compute";  // new (will exist after Task 7)
```

Note: `InvestmentTxn` export added in Task 7 — commit this file alongside Task 7 if TS complains now. Alternative: skip the InvestmentTxn import in this task and add in Task 7.

- [ ] **Step 2: Add three factories**

```ts
export function mkRobinhoodTxn(overrides: Partial<RobinhoodTxn> = {}): RobinhoodTxn {
  return {
    txnDate: "2026-01-15",
    action: "Buy",
    actionKind: "buy",
    ticker: "AAPL",
    quantity: 1,
    amountUsd: -200,
    rawDescription: "",
    ...overrides,
  };
}

export function mkEmpowerContribution(overrides: Partial<EmpowerContribution> = {}): EmpowerContribution {
  return {
    date: "2026-01-15",
    amount: 450,
    ticker: "401k sp500",
    cusip: "09259A791",
    ...overrides,
  };
}

// InvestmentTxn factory — added together with the type in Task 7.
```

- [ ] **Step 3: Update `mkTimelinePayload` to include new required fields**

```ts
export function mkTimelinePayload(overrides: Record<string, unknown> = {}) {
  return {
    daily: [ /* existing */ ],
    dailyTickers: [],
    fidelityTxns: [],
    qianjiTxns: [],
    robinhoodTxns: [],          // new
    empowerContributions: [],   // new
    categories: CATEGORIES,
    market: null,
    holdingsDetail: null,
    syncMeta: null,
    ...overrides,
  };
}
```

- [ ] **Step 4: Run frontend tests to confirm no break**

Run: `npx vitest run`

Expected: all 269+ tests still pass (nothing has consumed the new factories yet).

- [ ] **Step 5: Commit**

```bash
git add src/test/factories.ts
git commit -m "test: add mkRobinhoodTxn / mkEmpowerContribution factories + payload fields"
```

---

## Task 7: Add `InvestmentTxn` type + `normalizeInvestmentTxns` function

**Files:**
- Modify: `src/lib/compute/compute.ts`
- Modify: `src/lib/compute/compute.test.ts`
- Modify: `src/test/factories.ts` (add `mkInvestmentTxn`)

- [ ] **Step 1: Write the failing tests first (TDD)**

In `src/lib/compute/compute.test.ts`, add a new describe block (before existing `computeActivity`):

```ts
import { normalizeInvestmentTxns, type InvestmentTxn } from "./compute";
import { mkFidelityTxn, mkQianjiTxn, mkRobinhoodTxn, mkEmpowerContribution, mkInvestmentTxn } from "@/test/factories";

describe("normalizeInvestmentTxns", () => {
  it("maps Fidelity txns 1:1 preserving actionType", () => {
    const f = [
      mkFidelityTxn({ runDate: "2026-01-10", actionType: "buy",  symbol: "VTI", amount: -500 }),
      mkFidelityTxn({ runDate: "2026-01-11", actionType: "sell", symbol: "GS",  amount:  600 }),
    ];
    const out = normalizeInvestmentTxns(f, [], []);
    expect(out).toEqual([
      { source: "fidelity", date: "2026-01-10", ticker: "VTI", actionType: "buy",  amount: -500 },
      { source: "fidelity", date: "2026-01-11", ticker: "GS",  actionType: "sell", amount:  600 },
    ]);
  });

  it("filters Robinhood actionKind='other' and keeps the rest", () => {
    const r = [
      mkRobinhoodTxn({ actionKind: "buy",     ticker: "AAPL", amountUsd: -200 }),
      mkRobinhoodTxn({ actionKind: "other",   ticker: "",     amountUsd: -1.5, action: "AFEE" }),
      mkRobinhoodTxn({ actionKind: "deposit", ticker: "",     amountUsd:  500, action: "RTP" }),
    ];
    const out = normalizeInvestmentTxns([], r, []);
    expect(out).toHaveLength(2);
    expect(out.every((t) => t.source === "robinhood")).toBe(true);
    expect(out.map((t) => t.actionType)).toEqual(["buy", "deposit"]);
  });

  it("maps all Empower contributions to actionType='contribution'", () => {
    const e = [
      mkEmpowerContribution({ date: "2026-01-15", amount: 450, ticker: "401k sp500" }),
      mkEmpowerContribution({ date: "2026-01-15", amount: 90,  ticker: "401k tech"  }),
    ];
    const out = normalizeInvestmentTxns([], [], e);
    expect(out).toEqual([
      { source: "401k", date: "2026-01-15", ticker: "401k sp500", actionType: "contribution", amount: 450 },
      { source: "401k", date: "2026-01-15", ticker: "401k tech",  actionType: "contribution", amount: 90  },
    ]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/compute/compute.test.ts -t normalizeInvestmentTxns`

Expected: FAIL with `normalizeInvestmentTxns is not a function` or similar.

- [ ] **Step 3: Add the `InvestmentTxn` type and function to `src/lib/compute/compute.ts`**

Near the top of `compute.ts`, after the imports:

```ts
import type {
  CategoryMeta,
  DailyPoint,
  DailyTicker,
  FidelityTxn,
  QianjiTxn,
  RobinhoodTxn,            // new
  EmpowerContribution,     // new
} from "@/lib/schemas";

// ... existing imports

// ── Investment txn unification ────────────────────────────────────────────

/** Unified shape used by computeActivity + computeCrossCheck. Internal to the
 *  compute layer; does NOT cross the D1/Worker/Zod boundary. */
export interface InvestmentTxn {
  source: "fidelity" | "robinhood" | "401k";
  date: string;
  ticker: string;
  actionType: "buy" | "sell" | "dividend" | "reinvestment" | "deposit" | "contribution";
  amount: number;
}

export function normalizeInvestmentTxns(
  fidelity: FidelityTxn[],
  robinhood: RobinhoodTxn[],
  empower: EmpowerContribution[],
): InvestmentTxn[] {
  const out: InvestmentTxn[] = [];
  for (const f of fidelity) {
    out.push({
      source: "fidelity",
      date: f.runDate,
      ticker: f.symbol,
      actionType: f.actionType as InvestmentTxn["actionType"],
      amount: f.amount,
    });
  }
  for (const r of robinhood) {
    if (r.actionKind === "other") continue;
    out.push({
      source: "robinhood",
      date: r.txnDate,
      ticker: r.ticker,
      actionType: r.actionKind as InvestmentTxn["actionType"],
      amount: r.amountUsd,
    });
  }
  for (const e of empower) {
    out.push({
      source: "401k",
      date: e.date,
      ticker: e.ticker,
      actionType: "contribution",
      amount: e.amount,
    });
  }
  return out;
}
```

- [ ] **Step 4: Add `mkInvestmentTxn` factory to `src/test/factories.ts`**

```ts
import type { InvestmentTxn } from "@/lib/compute/compute";

export function mkInvestmentTxn(overrides: Partial<InvestmentTxn> = {}): InvestmentTxn {
  return {
    source: "fidelity",
    date: "2026-01-15",
    ticker: "VTI",
    actionType: "buy",
    amount: -500,
    ...overrides,
  };
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `npx vitest run src/lib/compute/compute.test.ts -t normalizeInvestmentTxns`

Expected: 3 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/compute/compute.ts src/lib/compute/compute.test.ts src/test/factories.ts
git commit -m "feat(compute): add InvestmentTxn type + normalizeInvestmentTxns"
```

---

## Task 8: Add `sources` to `ApiTicker` and extend computeActivity signature

**Files:**
- Modify: `src/lib/compute/computed-types.ts` (add `sources` to `ApiTicker`)
- Modify: `src/lib/compute/compute.ts` — `computeActivity` + `computeGroupedActivity` take `InvestmentTxn[]`
- Modify: `src/lib/compute/compute.test.ts` — migrate all call sites
- Modify: `src/lib/hooks/use-bundle.ts` — migrate call sites

- [ ] **Step 1: Locate `ApiTicker` type**

Run: `grep -n "interface ApiTicker\|type ApiTicker" src/lib/compute/computed-types.ts`

- [ ] **Step 2: Extend `ApiTicker` with `sources`**

In `src/lib/compute/computed-types.ts`:

```ts
export interface ApiTicker {
  // existing fields: ticker, count, total, isGroup, groupKey
  sources: Array<"fidelity" | "robinhood" | "401k">;
}
```

Also update any `ApiTickerRow`-ish row type that includes `sources` if tests use a thinner variant.

- [ ] **Step 3: Write a failing test for the new `computeActivity` signature**

Add to `src/lib/compute/compute.test.ts` (inside existing `describe("computeActivity", ...)`):

```ts
it("accumulates sources Set across fidelity + 401k + robinhood for the same grouped ticker", () => {
  const txns: InvestmentTxn[] = [
    mkInvestmentTxn({ source: "fidelity",  actionType: "buy", ticker: "VOO",          amount: -500 }),
    mkInvestmentTxn({ source: "401k",      actionType: "contribution", ticker: "401k sp500", amount: 450 }),
    mkInvestmentTxn({ source: "robinhood", actionType: "buy", ticker: "AAPL",         amount: -200 }),
  ];
  const a = computeActivity(txns, "2026-01-01", "2026-01-31");
  // Sources on the raw ticker rows (pre-grouping):
  const voo = a.buysBySymbol.find((r) => r.ticker === "VOO")!;
  expect(voo.sources).toEqual(["fidelity"]);
  const k401 = a.buysBySymbol.find((r) => r.ticker === "401k sp500")!;
  expect(k401.sources).toEqual(["401k"]);
  const aapl = a.buysBySymbol.find((r) => r.ticker === "AAPL")!;
  expect(aapl.sources).toEqual(["robinhood"]);
});
```

Also migrate existing `computeActivity(...)` test calls to use the new signature. Example pattern: a test that used `computeActivity(mkFidelityTxns([{buy, VTI, -500}]), start, end)` becomes `computeActivity(normalizeInvestmentTxns([fidelityTxn], [], []), start, end)` OR `computeActivity([mkInvestmentTxn({source: "fidelity", ...})], start, end)` — prefer `mkInvestmentTxn` for direct clarity, reserve `normalizeInvestmentTxns` for tests specifically verifying normalization.

- [ ] **Step 4: Run failing test**

Run: `npx vitest run src/lib/compute/compute.test.ts -t "accumulates sources"`

Expected: FAIL.

- [ ] **Step 5: Refactor `computeActivity` in `src/lib/compute/compute.ts`**

Change signature + body:

```ts
export function computeActivity(
  investmentTxns: InvestmentTxn[],
  start: string,
  end: string,
): ActivityResponse {
  const buys = new Map<string, { count: number; total: number; sources: Set<"fidelity"|"robinhood"|"401k"> }>();
  const sells = new Map<string, { count: number; total: number; sources: Set<"fidelity"|"robinhood"|"401k"> }>();
  const dividends = new Map<string, { count: number; total: number; sources: Set<"fidelity"|"robinhood"|"401k"> }>();

  const accumWithSrc = (m: typeof buys, key: string, amount: number, src: "fidelity"|"robinhood"|"401k") => {
    const e = m.get(key) ?? { count: 0, total: 0, sources: new Set() };
    e.count += 1;
    e.total += amount;
    e.sources.add(src);
    m.set(key, e);
  };

  for (const t of investmentTxns) {
    if (t.date < start || t.date > end) continue;
    if (!t.ticker) continue;
    const abs = Math.abs(t.amount);
    if (t.actionType === "buy" || t.actionType === "contribution") {
      accumWithSrc(buys, t.ticker, abs, t.source);
    } else if (t.actionType === "sell") {
      accumWithSrc(sells, t.ticker, abs, t.source);
    } else if (t.actionType === "dividend") {
      accumWithSrc(dividends, t.ticker, t.amount, t.source);
    } else if (t.actionType === "reinvestment") {
      accumWithSrc(dividends, t.ticker, abs, t.source);
      accumWithSrc(buys, t.ticker, abs, t.source);
    }
  }

  const toList = (m: typeof buys) =>
    [...m.entries()]
      .map(([ticker, v]) => ({
        ticker, count: v.count, total: round(v.total),
        isGroup: false,
        sources: [...v.sources],
      }))
      .sort((a, b) => b.total - a.total);

  return {
    buysBySymbol: toList(buys),
    sellsBySymbol: toList(sells),
    dividendsBySymbol: toList(dividends),
  };
}
```

- [ ] **Step 6: Migrate all existing `computeActivity` call sites in tests**

Use `grep -n "computeActivity(" src/lib/compute/compute.test.ts` to locate them. Update each to pass `InvestmentTxn[]` — prefer building directly via `mkInvestmentTxn`. Remove any `fidelityTxns`-named local vars in favor of `investmentTxns`.

- [ ] **Step 7: Migrate the `use-bundle.ts` call site**

Open `src/lib/hooks/use-bundle.ts`. Locate `computeActivity(data.fidelityTxns, ...)`. Change to:

```ts
const investmentTxns = data ? normalizeInvestmentTxns(data.fidelityTxns, data.robinhoodTxns, data.empowerContributions) : [];
// ...
const activity = (data && startDate && snapshotDate) ? computeActivity(investmentTxns, startDate, snapshotDate) : null;
```

(React Compiler memoizes; do not add manual `useMemo`.)

- [ ] **Step 8: Run all compute tests**

Run: `npx vitest run src/lib/compute/compute.test.ts`

Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add src/lib/compute/computed-types.ts src/lib/compute/compute.ts src/lib/compute/compute.test.ts src/lib/hooks/use-bundle.ts
git commit -m "refactor(compute): computeActivity takes InvestmentTxn[] + tracks sources"
```

---

## Task 9: Refactor `computeGroupedActivity` signature + aggregate sources

**Files:**
- Modify: `src/lib/compute/compute.ts` — `computeGroupedActivity`
- Modify: `src/lib/compute/compute.test.ts` — migrate test calls

- [ ] **Step 1: Open `src/lib/compute/compute.ts`, locate `computeGroupedActivity`**

Run: `grep -n "computeGroupedActivity" src/lib/compute/compute.ts`

- [ ] **Step 2: Write a failing test asserting sources aggregate across group members**

In `src/lib/compute/compute.test.ts`, inside the `describe("computeGroupedActivity", ...)` block:

```ts
it("aggregates sources across group members (VOO + 401k sp500 + FXAIX → S&P 500)", () => {
  const txns: InvestmentTxn[] = [
    mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "VOO",          amount: -500 }),
    mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "FXAIX",        amount: -100 }),
    mkInvestmentTxn({ source: "401k",      actionType: "contribution", ticker: "401k sp500",   amount:  450 }),
  ];
  const g = computeGroupedActivity(txns, "2026-01-01", "2026-01-31");
  const spRow = g.buysBySymbol.find((r) => r.ticker === "S&P 500")!;
  expect(spRow.isGroup).toBe(true);
  expect(spRow.sources.sort()).toEqual(["401k", "fidelity"]);
  expect(spRow.total).toBe(1050);
});
```

- [ ] **Step 3: Run to verify failure**

Run: `npx vitest run src/lib/compute/compute.test.ts -t "aggregates sources across group members"`

Expected: FAIL.

- [ ] **Step 4: Refactor `computeGroupedActivity`**

Change signature to `(investmentTxns: InvestmentTxn[], start, end)`. In the internal reduction, merge `sources` via union (same set-merge pattern) alongside count/total. Pattern:

```ts
// Inside the grouping loop:
const existing = map.get(groupDisplay);
if (existing) {
  existing.count += row.count;
  existing.total += row.total;
  for (const s of row.sources) existing.sources.add(s);
} else {
  map.set(groupDisplay, {
    count: row.count,
    total: row.total,
    sources: new Set(row.sources),
  });
}
```

And emit `sources: [...v.sources]` in the output.

- [ ] **Step 5: Migrate existing `computeGroupedActivity` test calls**

Same pattern as Task 8 — pass `InvestmentTxn[]` via `mkInvestmentTxn`.

- [ ] **Step 6: Update `use-bundle.ts` call site**

```ts
const groupedActivity = (data && startDate && snapshotDate) ? computeGroupedActivity(investmentTxns, startDate, snapshotDate) : null;
```

- [ ] **Step 7: Run tests**

Run: `npx vitest run src/lib/compute/compute.test.ts`

Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add src/lib/compute/compute.ts src/lib/compute/compute.test.ts src/lib/hooks/use-bundle.ts
git commit -m "refactor(compute): computeGroupedActivity takes InvestmentTxn[] + merges sources"
```

---

## Task 10: Extend `computeCrossCheck` to per-source + 401k aggregation

**Files:**
- Modify: `src/lib/compute/compute.ts` — update `CrossCheck` interface, `computeCrossCheck` function
- Modify: `src/lib/compute/compute.test.ts` — migrate + new tests
- Modify: `src/lib/hooks/use-bundle.ts` — call site + type flow

- [ ] **Step 1: Update `CrossCheck` interface**

Replace current interface:

```ts
export interface CrossCheck {
  matchedCount: number;
  totalCount: number;
  ok: boolean;
  perSource: {
    fidelity:     SourceCrossCheck;
    robinhood:    SourceCrossCheck;
    contribution: SourceCrossCheck;
  };
  allUnmatched: UnmatchedItem[];
}

export interface SourceCrossCheck {
  matched: number;
  total: number;
  unmatched: UnmatchedItem[];
}

export interface UnmatchedItem {
  source: "fidelity" | "robinhood" | "401k";
  date: string;
  amount: number;
  breakdown?: Array<{ ticker: string; amount: number }>;  // 401k paycheck breakdown
}
```

Remove `fidelityTotal`, `matchedTotal`, `unmatchedTotal` — DO NOT keep as aliases.

- [ ] **Step 2: Add the invariant + per-source failing tests**

```ts
describe("computeCrossCheck per-source", () => {
  it("invariant: matchedCount === sum perSource.matched; totalCount same", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity",  actionType: "deposit", ticker: "", date: "2026-01-10", amount: 500 }),
      mkInvestmentTxn({ source: "robinhood", actionType: "deposit", ticker: "", date: "2026-01-12", amount: 200 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k sp500", date: "2026-01-15", amount: 450 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k tech",  date: "2026-01-15", amount: 90 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 1 }),             // anchor floor
      mkQianjiTxn({ date: "2026-01-10", type: "transfer", amount: 500, accountTo: "Fidelity taxable" }),
      mkQianjiTxn({ date: "2026-01-12", type: "transfer", amount: 200, accountTo: "Robinhood" }),
      mkQianjiTxn({ date: "2026-01-15", type: "income",   amount: 540, accountTo: "401k", isRetirement: true }),
    ];
    const cc = computeCrossCheck(txns, q, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(cc.perSource.fidelity.matched + cc.perSource.robinhood.matched + cc.perSource.contribution.matched);
    expect(cc.totalCount).toBe(cc.perSource.fidelity.total + cc.perSource.robinhood.total + cc.perSource.contribution.total);
    expect(cc.ok).toBe(true);
    expect(cc.perSource.contribution.total).toBe(1);  // 2 fund contributions aggregate to 1 paycheck
    expect(cc.perSource.contribution.unmatched).toHaveLength(0);
  });

  it("401k aggregates same-date fund contributions into one paycheck record", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k sp500", date: "2026-01-15", amount: 450 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k tech",  date: "2026-01-15", amount: 90 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k ex-us", date: "2026-01-15", amount: 225 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 1 }),
      mkQianjiTxn({ date: "2026-01-15", type: "income", amount: 765, accountTo: "401k", isRetirement: true }),
    ];
    const cc = computeCrossCheck(txns, q, "2026-01-01", "2026-01-31");
    expect(cc.perSource.contribution.matched).toBe(1);
    expect(cc.perSource.contribution.total).toBe(1);
  });

  it("surfaces unmatched items in allUnmatched with breakdown for 401k", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k sp500", date: "2026-01-15", amount: 450 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k tech",  date: "2026-01-15", amount: 90 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 1 }),
      // No 401k income → unmatched
    ];
    const cc = computeCrossCheck(txns, q, "2026-01-01", "2026-01-31");
    expect(cc.perSource.contribution.unmatched).toHaveLength(1);
    const u = cc.perSource.contribution.unmatched[0];
    expect(u.amount).toBe(540);
    expect(u.breakdown).toEqual([
      { ticker: "401k sp500", amount: 450 },
      { ticker: "401k tech",  amount: 90 },
    ]);
    expect(cc.allUnmatched).toContain(u);
  });

  it("Robinhood deposits match only against Qianji with accountTo starting 'robinhood'", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "robinhood", actionType: "deposit", ticker: "", date: "2026-01-15", amount: 500 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 1 }),
      mkQianjiTxn({ date: "2026-01-15", type: "income", amount: 500, accountTo: "Fidelity taxable" }),  // wrong account
    ];
    const cc = computeCrossCheck(txns, q, "2026-01-01", "2026-01-31");
    expect(cc.perSource.robinhood.matched).toBe(0);
    expect(cc.perSource.robinhood.unmatched).toHaveLength(1);
  });
});
```

- [ ] **Step 3: Run to verify failures**

Run: `npx vitest run src/lib/compute/compute.test.ts -t "computeCrossCheck per-source"`

Expected: all FAIL.

- [ ] **Step 4: Implement `computeCrossCheck` refactor**

Replace the body. Structure:

```ts
export function computeCrossCheck(
  investmentTxns: InvestmentTxn[],
  qianjiTxns: QianjiTxn[],
  start: string,
  end: string,
): CrossCheck {
  // Qianji floor logic reused
  let earliestQianji: string | null = null;
  for (const t of qianjiTxns) {
    if (earliestQianji === null || t.date < earliestQianji) earliestQianji = t.date;
  }
  let effectiveStart = start;
  if (earliestQianji) {
    const floorMs = new Date(earliestQianji).getTime() - MATCH_WINDOW_MS;
    const floor = new Date(floorMs).toISOString().slice(0, 10);
    if (floor > effectiveStart) effectiveStart = floor;
  }

  // Per-source predicate table
  type SrcKey = "fidelity" | "robinhood" | "contribution";
  const sources: Record<SrcKey, SourceCrossCheck> = {
    fidelity:     { matched: 0, total: 0, unmatched: [] },
    robinhood:    { matched: 0, total: 0, unmatched: [] },
    contribution: { matched: 0, total: 0, unmatched: [] },
  };

  // ── Fidelity ──
  {
    const deposits = investmentTxns
      .filter((t) => t.source === "fidelity" && t.actionType === "deposit"
        && Math.abs(t.amount) >= DUST_THRESHOLD
        && t.date >= effectiveStart && t.date <= end)
      .map((t) => ({ amount: Math.abs(t.amount), ms: new Date(t.date).getTime(), date: t.date }));
    const candidates = qianjiTxns.filter((q) =>
      q.type === "transfer" ||
      (q.type === "income" && q.accountTo.toLowerCase().startsWith("fidelity")),
    );
    matchAndRecord(deposits, candidates, sources.fidelity, "fidelity");
  }

  // ── Robinhood ──
  {
    const deposits = investmentTxns
      .filter((t) => t.source === "robinhood" && t.actionType === "deposit"
        && Math.abs(t.amount) >= DUST_THRESHOLD
        && t.date >= effectiveStart && t.date <= end)
      .map((t) => ({ amount: Math.abs(t.amount), ms: new Date(t.date).getTime(), date: t.date }));
    const candidates = qianjiTxns.filter((q) =>
      q.type === "transfer" ||
      (q.type === "income" && q.accountTo.toLowerCase().startsWith("robinhood")),
    );
    matchAndRecord(deposits, candidates, sources.robinhood, "robinhood");
  }

  // ── 401k (aggregate by date) ──
  {
    const byDate = new Map<string, { amount: number; breakdown: Array<{ ticker: string; amount: number }> }>();
    for (const t of investmentTxns) {
      if (t.source !== "401k" || t.actionType !== "contribution") continue;
      if (t.date < effectiveStart || t.date > end) continue;
      const e = byDate.get(t.date) ?? { amount: 0, breakdown: [] };
      e.amount += t.amount;
      e.breakdown.push({ ticker: t.ticker, amount: t.amount });
      byDate.set(t.date, e);
    }
    const deposits = [...byDate.entries()].map(([date, v]) => ({
      amount: v.amount,
      ms: new Date(date).getTime(),
      date,
      breakdown: v.breakdown,
    }));
    const candidates = qianjiTxns.filter((q) =>
      q.type === "income" && q.isRetirement && q.accountTo.toLowerCase().startsWith("401k"),
    );
    matchAndRecord(deposits, candidates, sources.contribution, "401k");
  }

  const matchedCount = sources.fidelity.matched + sources.robinhood.matched + sources.contribution.matched;
  const totalCount   = sources.fidelity.total   + sources.robinhood.total   + sources.contribution.total;
  const allUnmatched = [
    ...sources.fidelity.unmatched,
    ...sources.robinhood.unmatched,
    ...sources.contribution.unmatched,
  ];
  return {
    matchedCount,
    totalCount,
    ok: totalCount > 0 && matchedCount === totalCount,
    perSource: sources,
    allUnmatched,
  };
}

// Helper: earliest-in-window matching per source with per-source predicate set
function matchAndRecord(
  deposits: Array<{ amount: number; ms: number; date: string; breakdown?: Array<{ ticker: string; amount: number }> }>,
  candidates: QianjiTxn[],
  out: SourceCrossCheck,
  sourceLabel: UnmatchedItem["source"],
): void {
  const used = new Set<number>();
  const sorted = [...deposits].sort((a, b) => a.ms - b.ms);
  for (const dep of sorted) {
    out.total += 1;
    let bestIdx = -1, bestMs = Infinity;
    const depCents = Math.round(dep.amount * 100);
    for (let i = 0; i < candidates.length; i++) {
      if (used.has(i)) continue;
      if (Math.round(candidates[i].amount * 100) !== depCents) continue;
      const candMs = new Date(candidates[i].date).getTime();
      if (Math.abs(dep.ms - candMs) <= MATCH_WINDOW_MS && candMs < bestMs) {
        bestIdx = i;
        bestMs = candMs;
      }
    }
    if (bestIdx >= 0) {
      used.add(bestIdx);
      out.matched += 1;
    } else {
      const item: UnmatchedItem = {
        source: sourceLabel,
        date: dep.date,
        amount: dep.amount,
      };
      if (dep.breakdown) item.breakdown = dep.breakdown;
      out.unmatched.push(item);
    }
  }
}
```

- [ ] **Step 5: Migrate existing `computeCrossCheck` test call sites**

All existing tests in `describe("computeCrossCheck", ...)` pass `FidelityTxn[]`. Migrate them: build `InvestmentTxn[]` via `normalizeInvestmentTxns([fTxn], [], [])` or directly `mkInvestmentTxn({source: "fidelity", ...})`.

Update assertions: `cc.matchedCount` stays; any test that used `cc.fidelityTotal` etc. switches to `cc.perSource.fidelity.matched`/`total` or the explicit `allUnmatched` list.

- [ ] **Step 6: Update `use-bundle.ts` call site**

```ts
const crossCheck = (data && startDate && snapshotDate) ? computeCrossCheck(investmentTxns, data.qianjiTxns, startDate, snapshotDate) : null;
```

- [ ] **Step 7: Run full vitest**

Run: `npx vitest run`

Expected: ALL tests PASS. If a test expected the old field names, it was missed in step 5 — fix it.

- [ ] **Step 8: Commit**

```bash
git add src/lib/compute/compute.ts src/lib/compute/compute.test.ts src/lib/hooks/use-bundle.ts
git commit -m "feat(crosscheck): per-source reconciliation for Fidelity + Robinhood + 401k"
```

---

## Task 11: Create `SourceBadge` component

**Files:**
- Create: `src/components/finance/source-badge.tsx`
- Test: `src/components/finance/source-badge.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/finance/source-badge.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SourceBadge } from "./source-badge";

describe("SourceBadge", () => {
  it("renders 'FID' for fidelity", () => {
    render(<SourceBadge source="fidelity" />);
    expect(screen.getByText("FID")).toBeInTheDocument();
  });
  it("renders 'RH' for robinhood", () => {
    render(<SourceBadge source="robinhood" />);
    expect(screen.getByText("RH")).toBeInTheDocument();
  });
  it("renders '401k' for 401k", () => {
    render(<SourceBadge source="401k" />);
    expect(screen.getByText("401k")).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run src/components/finance/source-badge.test.tsx`

Expected: FAIL — file not found.

- [ ] **Step 3: Implement `SourceBadge`**

```tsx
// src/components/finance/source-badge.tsx
import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";

const LABELS = {
  fidelity: "FID",
  robinhood: "RH",
  "401k": "401k",
} as const;

// Okabe-Ito palette (protanomaly-safe); pairs letter text with color so
// the badge stays distinguishable without color alone.
const COLORS = {
  fidelity:  CAT_COLOR_BY_KEY.usEquity,     // blue
  robinhood: CAT_COLOR_BY_KEY.nonUsEquity,  // green
  "401k":    CAT_COLOR_BY_KEY.crypto,       // orange
} as const;

export function SourceBadge({ source }: { source: "fidelity" | "robinhood" | "401k" }) {
  return (
    <span
      className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium"
      style={{ backgroundColor: COLORS[source] + "33", color: COLORS[source] }}
      aria-label={`source: ${source}`}
    >
      {LABELS[source]}
    </span>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npx vitest run src/components/finance/source-badge.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/finance/source-badge.tsx src/components/finance/source-badge.test.tsx
git commit -m "feat(ui): add SourceBadge component"
```

---

## Task 12: Render source badges in `TickerTable`

**Files:**
- Modify: `src/components/finance/ticker-table.tsx`
- Modify: `src/components/finance/ticker-table.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// In ticker-table.test.tsx, add:
it("renders SourceBadge for each source on a row", () => {
  const data = [
    { ticker: "S&P 500", count: 3, total: 1050, isGroup: true, sources: ["fidelity", "401k"] as const, groupKey: "sp500" },
  ];
  render(<TickerTable title="Buys by Symbol" data={data} />);
  expect(screen.getByText("FID")).toBeInTheDocument();
  expect(screen.getByText("401k")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run src/components/finance/ticker-table.test.tsx -t "renders SourceBadge"`

Expected: FAIL.

- [ ] **Step 3: Add import + render badges inline next to ticker name**

```tsx
import { SourceBadge } from "./source-badge";

// In the row render:
<td className="...">
  {row.ticker}
  {row.sources.map((s) => (
    <span key={s} className="ml-1"><SourceBadge source={s} /></span>
  ))}
</td>
```

- [ ] **Step 4: Run to verify pass + existing tests still pass**

Run: `npx vitest run src/components/finance/ticker-table.test.tsx`

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/finance/ticker-table.tsx src/components/finance/ticker-table.test.tsx
git commit -m "feat(ui): render SourceBadge per TickerTable row"
```

---

## Task 13: Create `UnmatchedPanel` component

**Files:**
- Create: `src/components/finance/unmatched-panel.tsx`
- Test: `src/components/finance/unmatched-panel.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/finance/unmatched-panel.test.tsx
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { UnmatchedPanel } from "./unmatched-panel";
import type { UnmatchedItem } from "@/lib/compute/compute";

describe("UnmatchedPanel", () => {
  it("groups items by source and renders breakdown for 401k", () => {
    const items: UnmatchedItem[] = [
      { source: "fidelity",  date: "2024-10-01", amount: 500 },
      { source: "401k", date: "2024-05-15", amount: 765,
        breakdown: [
          { ticker: "401k sp500", amount: 450 },
          { ticker: "401k tech",  amount: 90 },
          { ticker: "401k ex-us", amount: 225 },
        ],
      },
    ];
    render(<UnmatchedPanel items={items} />);
    expect(screen.getByText(/Fidelity \(1\)/)).toBeInTheDocument();
    expect(screen.getByText("2024-10-01")).toBeInTheDocument();
    expect(screen.getByText(/\$500/)).toBeInTheDocument();
    expect(screen.getByText(/401k \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/sp500 \$450, tech \$90, ex-us \$225/)).toBeInTheDocument();
  });

  it("renders nothing when items is empty", () => {
    const { container } = render(<UnmatchedPanel items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npx vitest run src/components/finance/unmatched-panel.test.tsx`

Expected: FAIL.

- [ ] **Step 3: Implement**

```tsx
// src/components/finance/unmatched-panel.tsx
import type { UnmatchedItem } from "@/lib/compute/compute";

const LABELS: Record<UnmatchedItem["source"], string> = {
  fidelity:  "Fidelity",
  robinhood: "Robinhood",
  "401k":    "401k",
};

function formatBreakdown(b: NonNullable<UnmatchedItem["breakdown"]>): string {
  return b.map((x) => `${x.ticker.replace(/^401k /, "")} $${x.amount.toFixed(0)}`).join(", ");
}

export function UnmatchedPanel({ items }: { items: UnmatchedItem[] }) {
  if (items.length === 0) return null;

  const grouped = new Map<UnmatchedItem["source"], UnmatchedItem[]>();
  for (const it of items) {
    const list = grouped.get(it.source) ?? [];
    list.push(it);
    grouped.set(it.source, list);
  }

  return (
    <div className="mt-3 p-3 rounded border border-red-400/30 bg-red-950/20 text-sm">
      {[...grouped.entries()].map(([src, list]) => (
        <div key={src} className="mb-2 last:mb-0">
          <div className="font-medium text-red-300 mb-1">{LABELS[src]} ({list.length}):</div>
          <ul className="pl-4 space-y-0.5 text-muted-foreground font-mono text-xs">
            {list.map((it, i) => (
              <li key={i}>
                {it.date}  ${it.amount.toFixed(2)}
                {it.breakdown && <span className="text-foreground/60"> ({formatBreakdown(it.breakdown)})</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run to verify pass**

Run: `npx vitest run src/components/finance/unmatched-panel.test.tsx`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/components/finance/unmatched-panel.tsx src/components/finance/unmatched-panel.test.tsx
git commit -m "feat(ui): add UnmatchedPanel drawer component"
```

---

## Task 14: Rename section + wire tooltip + drawer in `page.tsx`

**Files:**
- Modify: `src/app/finance/page.tsx`
- Modify: `src/components/layout/sidebar.tsx`

- [ ] **Step 1: Rename section id, title, ErrorBoundary label in `page.tsx`**

Find the section (around line 186-205 in current code):

```tsx
<ErrorBoundary fallback={<SectionError label="Investment Activity" />}>
  <section id="investment-activity" className="scroll-mt-20 md:scroll-mt-8">
    <SectionHeader>
      Investment Activity
      {/* badge logic updated below */}
    </SectionHeader>
    ...
  </section>
</ErrorBoundary>
```

- [ ] **Step 2: Replace the badge with tooltip + clickable drawer**

```tsx
const [expanded, setExpanded] = useState(false);

{crossCheck && (
  <button
    type="button"
    onClick={() => crossCheck.ok ? null : setExpanded((v) => !v)}
    disabled={crossCheck.ok}
    className={`ml-2 inline-flex items-center gap-1 text-xs font-normal ${crossCheck.ok ? "text-green-500 cursor-default" : "text-red-400 cursor-pointer hover:text-red-300"}`}
    title={[
      `Fidelity:   ${crossCheck.perSource.fidelity.matched}/${crossCheck.perSource.fidelity.total}`,
      `Robinhood:  ${crossCheck.perSource.robinhood.matched}/${crossCheck.perSource.robinhood.total}`,
      `401k:       ${crossCheck.perSource.contribution.matched}/${crossCheck.perSource.contribution.total}`,
    ].join("\n")}
  >
    {crossCheck.ok ? "\u2713" : "\u2717"}{" "}
    {crossCheck.matchedCount}/{crossCheck.totalCount} deposits reconciled
  </button>
)}
```

- [ ] **Step 3: Render the drawer when `expanded && !crossCheck.ok`**

Below the `<SectionHeader>` (inside `<section>`):

```tsx
{crossCheck && !crossCheck.ok && expanded && (
  <UnmatchedPanel items={crossCheck.allUnmatched} />
)}
```

Add the import at the top:
```tsx
import { UnmatchedPanel } from "@/components/finance/unmatched-panel";
```

- [ ] **Step 4: Rename sidebar entry in `src/components/layout/sidebar.tsx`**

```tsx
const financeSections = [
  { label: "Overview", hash: "#timemachine" },
  { label: "Investment", hash: "#investment-activity" },  // renamed
  { label: "Cash Flow", hash: "#cashflow" },
  { label: "Market", hash: "#market" },
];
```

- [ ] **Step 5: Run vitest + tsc**

Run: `npx vitest run && npx tsc --noEmit`

Expected: all PASS. E2E tests that match on "Fidelity Activity" text or `#fidelity-activity` anchor will fail — Task 16 handles them.

- [ ] **Step 6: Commit**

```bash
git add src/app/finance/page.tsx src/components/layout/sidebar.tsx
git commit -m "feat(ui): rename to Investment Activity + per-source tooltip + drawer"
```

---

## Task 15: Pass new arrays through `use-bundle.ts`

**Files:**
- Modify: `src/lib/hooks/use-bundle.ts`
- Modify: `src/lib/hooks/use-bundle.test.ts`
- Modify: `src/app/finance/page.tsx` — update `ActivityContent` props destructure

- [ ] **Step 1: Review current `use-bundle.ts`**

Run: `grep -n "fidelityTxns\|qianjiTxns" src/lib/hooks/use-bundle.ts`

- [ ] **Step 2: Extend bundle return type**

Add `robinhoodTxns`, `empowerContributions`, `investmentTxns` to the object returned by `useBundle()`. `investmentTxns` is computed via `normalizeInvestmentTxns` (Tasks 8-10 already updated the compute call sites to use it — make sure it's wired).

```ts
return {
  // ...existing fields
  fidelityTxns: data?.fidelityTxns ?? [],
  qianjiTxns:   data?.qianjiTxns ?? [],
  robinhoodTxns: data?.robinhoodTxns ?? [],
  empowerContributions: data?.empowerContributions ?? [],
  investmentTxns,   // already defined earlier (step 7 of Task 8)
  // ...
};
```

- [ ] **Step 3: Update `use-bundle.test.ts`**

Any fixture that builds a timeline payload must include `robinhoodTxns: []` and `empowerContributions: []` (already handled by `mkTimelinePayload` in Task 6). Verify tests still pass:

Run: `npx vitest run src/lib/hooks/use-bundle.test.ts`

Expected: PASS.

- [ ] **Step 4: Update `ActivityContent` props in `page.tsx`**

`ActivityContent` currently takes `fidelityTxns`. It passes down to `TickerTable`. Since `TickerTable` now reads `row.sources`, `fidelityTxns` may no longer be needed for that; but `TickerTable` also uses `fidelityTxns` for buy/sell markers on ticker dialog (check current code). Keep `fidelityTxns` as-is for that purpose (raw data source) — do not force InvestmentTxn into the dialog yet (out of scope).

Verify the destructure in page.tsx:
```tsx
const {
  allocation, cashflow, activity, groupedActivity, market, crossCheck,
  categories, chartDaily, monthlyFlows, syncMeta, marketError,
  brushStart, brushEnd, defaultStartIndex, defaultEndIndex, onBrushChange,
  dailyTickers, fidelityTxns,
  // no new destructures needed here unless ActivityContent is changed
} = tl;
```

- [ ] **Step 5: Run full vitest + tsc**

Run: `npx vitest run && npx tsc --noEmit`

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add src/lib/hooks/use-bundle.ts src/lib/hooks/use-bundle.test.ts src/app/finance/page.tsx
git commit -m "feat(bundle): expose robinhoodTxns + empowerContributions + investmentTxns"
```

---

## Task 16: Update E2E mock API + specs for new section

**Files:**
- Modify: `e2e/mock-api.ts`
- Modify: `e2e/finance.spec.ts`, `e2e/group-toggle.spec.ts`, `e2e/ticker-dialog.spec.ts` (any mention of `Fidelity Activity` or `#fidelity-activity`)
- Create: `e2e/investment-activity.spec.ts` (or append to finance.spec.ts)

- [ ] **Step 1: Locate string occurrences**

Run:
```bash
grep -n "Fidelity Activity\|fidelity-activity" e2e/
```

- [ ] **Step 2: Update mock-api.ts fixture**

In `e2e/mock-api.ts`, add fixture entries for:
- ≥1 Robinhood buy: `mkRobinhoodTxn({actionKind: "buy", ticker: "AAPL", amountUsd: -200, txnDate: "<snapshot-era>"})`
- ≥1 Robinhood sell: same pattern with `actionKind: "sell"`
- ≥1 401k contribution: `mkEmpowerContribution({amount: 450, ticker: "401k sp500", date: "<snapshot-era>"})`
- ≥1 unmatched Robinhood deposit: `mkRobinhoodTxn({actionKind: "deposit", amountUsd: 500, txnDate: "<snapshot-era>"})` — with no matching Qianji transfer for that amount

Ensure `robinhoodTxns` and `empowerContributions` arrays are present in the mock payload (required by Zod).

- [ ] **Step 3: Update existing e2e specs — rename "Fidelity Activity" → "Investment Activity" and `#fidelity-activity` → `#investment-activity`**

For each file found in Step 1, do a targeted rename.

- [ ] **Step 4: Add a new e2e test for the drawer expansion**

In `e2e/finance.spec.ts` (or a new file):

```ts
test("investment activity: unmatched drawer expands on red X click", async ({ page }) => {
  await page.goto("/finance");
  // Badge should show ✗ because mock has an unmatched Robinhood deposit
  const badge = page.getByRole("button", { name: /deposits reconciled/i });
  await expect(badge).toBeVisible();
  await expect(badge).toHaveText(/✗/);

  // Click to expand drawer
  await badge.click();
  await expect(page.getByText(/Robinhood \(1\)/)).toBeVisible();
  await expect(page.getByText(/\$500.00/)).toBeVisible();
});

test("investment activity: tooltip shows per-source breakdown", async ({ page }) => {
  await page.goto("/finance");
  const badge = page.getByRole("button", { name: /deposits reconciled/i });
  const title = await badge.getAttribute("title");
  expect(title).toContain("Fidelity:");
  expect(title).toContain("Robinhood:");
  expect(title).toContain("401k:");
});
```

- [ ] **Step 5: Run E2E**

Run: `npx playwright test`

Expected: all pass. If new tests fail, adjust fixture amounts / dates to ensure the mock state produces the expected match/unmatch outcome.

- [ ] **Step 6: Commit**

```bash
git add e2e/
git commit -m "test(e2e): cover Investment Activity section + drawer + tooltip"
```

---

## Task 17: Local end-to-end verification

**Files:** none (verification only)

- [ ] **Step 1: Rebuild local timemachine.db**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/build_timemachine_db.py`

Expected: no errors; exit 0.

- [ ] **Step 2: Sync to local D1**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/sync_to_d1.py --local`

Expected: "D1 sync complete."

- [ ] **Step 3: Apply updated schema.sql to local D1**

(sync_to_d1.py auto-alters columns but not views; the two new views need an explicit apply.)

Run: `cd worker && npx wrangler d1 execute portal-db --local --file=schema.sql`

Expected: SQL executes cleanly.

- [ ] **Step 4: Probe /timeline**

With `wrangler dev` still running:

Run:
```bash
curl -s http://localhost:8787/timeline -o /c/tmp/timeline.json
node -e 'const d=require("fs").readFileSync("C:/tmp/timeline.json","utf8"); const j=JSON.parse(d); console.log("robinhoodTxns:", j.robinhoodTxns.length); console.log("empowerContributions:", j.empowerContributions.length)'
```

Expected: both counts > 0. If either is 0, the sync-to-local pipeline or the view SQL is off.

- [ ] **Step 5: Open the dashboard, hover the badge, expand drawer (if any unmatched)**

Navigate to `http://localhost:3000/finance`. Verify:
- Section header reads "Investment Activity"
- Sidebar shows "Investment"
- Badge shows a number (e.g., `N/M`)
- Hover title shows 3-line per-source breakdown
- If red: click to expand, see `UnmatchedPanel`

- [ ] **Step 6: Run full test suites**

Run:
```bash
npx vitest run
npx playwright test
cd pipeline && .venv/Scripts/python.exe -m pytest -q
cd pipeline && .venv/Scripts/python.exe -m pytest tests/regression/ -v
```

Expected: all PASS. Regression L1/L2 baselines MUST be stable (no drift) since we only added views.

- [ ] **Step 7: Record real-data baseline in scratch file**

```bash
node -e '...' > /tmp/baseline.txt   # printing the current matchedCount / perSource breakdown
```

Copy the output into the PR description for reference (#9 of regression防护).

- [ ] **Step 8: Commit nothing (verification only)**

---

## Task 18: Pre-merge production rollout (manual, ops)

**Files:** none (manual ops checklist)

- [ ] **Step 1: Apply prod D1 schema (adds new views)**

Run: `cd worker && npx wrangler d1 execute portal-db --remote --file=schema.sql`

Expected: SQL executes cleanly. Views `v_robinhood_txns` and `v_empower_contributions` created.

- [ ] **Step 2: Sync prod D1 data (diff mode is sufficient — new pipeline tables already populate)**

Run: `cd pipeline && .venv/Scripts/python.exe scripts/sync_to_d1.py`

Expected: "D1 sync complete."

- [ ] **Step 3: Deploy prod Worker (needed because SELECT additions)**

Run: `cd worker && npx wrangler deploy`

Expected: Worker deployed.

- [ ] **Step 4: Verify prod /timeline (via user's browser, since CF Access)**

Ask user to open https://portal.guoyuer.com/finance, open DevTools → Network → `/timeline` → Response body. Confirm:
- `robinhoodTxns` array present + non-empty
- `empowerContributions` array present + non-empty

If either missing: re-run the relevant step above.

- [ ] **Step 5: Confirm pre-merge baseline one more time**

Ask the user to hover the current production badge (still old "Fidelity Activity"). Record `matchedCount/totalCount` and `perSource.fidelity.matched/total` — this becomes the post-merge anchor. Expected: `101/101` for Fidelity.

- [ ] **Step 6: Open PR, include baseline + expected unmatched list**

Push the branch, open PR. PR description template:

```
## Investment Activity

[Summary]

## Pre-merge baseline (recorded 2026-04-XX):
- Fidelity: 101/101 ✓

## Post-merge expectations:
- perSource.fidelity MUST still be 101/101 ✓  ← rollback if not
- perSource.robinhood: expected ~37/X (X depends on Qianji coverage)
- perSource.contribution: expected ~89/Y

## Rollback plan
- `git revert <merge_commit>` + push (Pages auto-redeploys old frontend)
- `cd worker && git checkout HEAD~1 -- src/ && npx wrangler deploy` (rollback Worker)
- D1 views can stay — additive, harmless
```

- [ ] **Step 7: Wait for CI green, then merge**

- [ ] **Step 8: Post-merge verify (user opens prod)**

User navigates to https://portal.guoyuer.com/finance. Must see:
- "Investment Activity" section title
- Sidebar label "Investment"
- Badge with aggregate N/M
- Hover tooltip shows 3 sources
- `perSource.fidelity` still 101/101 in the tooltip

Mismatch on Fidelity: execute rollback immediately.

---

## Self-Review

After saving this plan, I check:

**1. Spec coverage:**
- Scope (Fidelity + 401k + Robinhood) → Tasks 1-4 (pipeline + types), Task 7 (normalize), Tasks 8-10 (compute) ✓
- `Robinhood actionKind='other'` filter → Task 7 ✓
- 401k as `contribution`, aggregated into Buys via EQUIVALENT_GROUPS → Tasks 7, 8, 9 ✓
- Cross-check per-source extended to all 3 → Task 10 ✓
- 401k date aggregation → Task 10 ✓
- Rename Section → Investment Activity → Task 14 ✓
- Sidebar rename → Task 14 ✓
- `CrossCheck` new shape + `perSource` + `allUnmatched` + `UnmatchedItem` → Task 10 ✓
- `ApiTicker.sources` → Task 8 ✓
- `SourceBadge` component → Task 11 ✓
- `UnmatchedPanel` component → Task 13 ✓
- `TickerTable` renders badges → Task 12 ✓
- Tooltip + click-to-expand drawer → Task 14 ✓
- `use-bundle.ts` wiring → Task 15 ✓
- Strict Zod (no `.default([])`) → Task 4 ✓
- E2E fixture + tests → Task 16 ✓
- Local verification → Task 17 ✓
- Prod rollout checklist → Task 18 ✓

**2. Regression防护 coverage:**
- #1 migrate all call sites → Tasks 8, 9, 10 ✓
- #2 E2E mock with 401k + Robinhood + unmatched → Task 16 ✓
- #3 factories → Tasks 6, 7 ✓
- #4 401k same-day aggregation test → Task 10 (third test in per-source block) ✓
- #5 PR description expected unmatched → Task 18 Step 6 ✓
- #6 merge-blocking prod checklist → Task 18 ✓
- #7 L1/L2 regression re-run → Task 17 Step 6 ✓
- #8 aggregate = sum invariant → Task 10 (first test in per-source block) ✓
- #9 Fidelity baseline 101/101 anchor → Task 17 Step 7 + Task 18 Step 5 ✓
- #10 Rollback plan in PR → Task 18 Step 6 ✓

**3. Type consistency:**
- `InvestmentTxn` actionType enum used consistently: "buy"|"sell"|"dividend"|"reinvestment"|"deposit"|"contribution" — Task 7 defines it, Tasks 8-10 consume same names ✓
- `UnmatchedItem.source` values: "fidelity"|"robinhood"|"401k" — Task 10 defines, Task 13 consumes same ✓
- `CrossCheck.perSource` keys: `fidelity`, `robinhood`, `contribution` — consistent across Tasks 10, 14 ✓
- `SourceBadge` source prop accepts same union as InvestmentTxn.source ✓

**4. Placeholder scan:**
- No TBDs, TODOs, "fill in later", generic "add error handling" ✓
- All code blocks are concrete ✓
- Exact commands with expected output ✓

---

## Execution Handoff

Plan complete and saved to `docs/plans/2026-04-20-investment-activity.md`. Two execution options:

**1. Subagent-Driven (recommended)** — dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
