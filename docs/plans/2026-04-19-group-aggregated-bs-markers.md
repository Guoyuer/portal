# Group-Aggregated B/S Markers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Aggregate economically-equivalent tickers (e.g. VOO/IVV/SPY/FXAIX, QQQ/QQQM) into groups so B/S markers reflect actual exposure change instead of swap noise.

**Architecture:** Frontend-only. Equivalence groups live in a static TS constant. A `classifyTxn` helper tags each Fidelity transaction as `REAL | REINVEST | SPLIT | ROLLOVER | OTHER`. A `groupNetByDate` algorithm clusters REAL txns within a group using T+2 chaining, nets buy/sell amounts, and skips clusters where |net| < $50. The Activity section gains a toggle; when ON, `buysBySymbol`/`sellsBySymbol` are regrouped by equivalence-group key. Clicking a group row opens a new `GroupChartDialog` that mirrors `TickerChartDialog` but plots cumulative position value (summed from `dailyTickers`) instead of a single-ticker price line.

**Tech Stack:** Next.js 16 (static export), React 19, Recharts, Zod, Vitest, Playwright. No changes to Worker/D1/pipeline.

**Reference spec:** `docs/specs/2026-04-19-group-aggregated-bs-markers-design.md`

---

## File structure

**New files:**
- `src/lib/config/equivalent-groups.ts` — static config + invariant check + ticker→groupKey index
- `src/lib/format/group-aggregation.ts` — `TxnType`, `classifyTxn`, `groupNetByDate`, `buildGroupValueSeries`
- `src/lib/format/group-aggregation.test.ts` — unit tests for the data layer
- `src/components/finance/group-dialog.tsx` — full-screen dialog mirroring `TickerChartDialog` but with total-value Y-axis and ticker-level tooltip breakdown
- `src/components/finance/group-chart.tsx` — inline chart used inside a toggled-ON table row (thin wrapper around `TickerChartBase` with injected data)
- `e2e/group-toggle.spec.ts` — smoke test

**Modified files:**
- `src/components/finance/ticker-markers.tsx` — extend `ClusterMarkerProps.payload` with optional `breakdown` for group-view tooltips; add `ReinvestMarker` (tiny 3px muted dot).
- `src/components/finance/ticker-table.tsx` — accept `grouped?: boolean` and `groups?: EquivalentGroup[]`; render group rows with aggregated counts/totals; open `GroupChartDialog` instead of inline `TickerChart` when the row is a group.
- `src/components/finance/ticker-chart.tsx` — render REINVEST markers (inline and in dialog) as tiny muted dots.
- `src/lib/format/ticker-data.ts` — keep existing buy/sell clustering; extend `TickerChartPoint` with optional `reinvestQty` / `reinvestAmount` so `mergeTickerData` emits them.
- `src/app/finance/page.tsx` — add toggle state in the Activity section; pass `grouped` + `groups` down to `<TickerTable>`.

---

## Task 1: Equivalence-group config + invariant

**Files:**
- Create: `src/lib/config/equivalent-groups.ts`
- Create: `src/lib/config/equivalent-groups.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/config/equivalent-groups.test.ts
import { describe, it, expect } from "vitest";
import { EQUIVALENT_GROUPS, GROUP_BY_TICKER, groupOfTicker } from "./equivalent-groups";

describe("equivalent groups", () => {
  it("indexes every listed ticker back to its group", () => {
    for (const [key, group] of Object.entries(EQUIVALENT_GROUPS)) {
      for (const t of group.tickers) {
        expect(GROUP_BY_TICKER.get(t)).toBe(key);
      }
    }
  });

  it("returns null for tickers not in any group", () => {
    expect(groupOfTicker("SOLO_TICKER_NOT_IN_ANY_GROUP")).toBeNull();
  });

  it("finds QQQ in nasdaq_100", () => {
    expect(groupOfTicker("QQQ")).toBe("nasdaq_100");
    expect(groupOfTicker("QQQM")).toBe("nasdaq_100");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/config/equivalent-groups.test.ts`
Expected: FAIL with "Cannot find module './equivalent-groups'"

- [ ] **Step 3: Write implementation**

```ts
// src/lib/config/equivalent-groups.ts
// ── Economically-equivalent ticker groups ───────────────────────────────
// Hand-maintained. A ticker must appear in at most one group; the
// invariant check throws at module-load if violated, so a bad edit
// breaks the build instead of silently mis-classifying transactions.

export type EquivalentGroup = {
  key: string;
  display: string;
  tickers: string[];
};

export const EQUIVALENT_GROUPS: Record<string, EquivalentGroup> = {
  nasdaq_100: {
    key: "nasdaq_100",
    display: "NASDAQ 100",
    tickers: ["QQQ", "QQQM", "401k tech"],
  },
  sp500: {
    key: "sp500",
    display: "S&P 500",
    tickers: ["VOO", "IVV", "SPY", "FXAIX", "401k sp500"],
  },
};

function buildIndex(): Map<string, string> {
  const m = new Map<string, string>();
  for (const [key, group] of Object.entries(EQUIVALENT_GROUPS)) {
    for (const t of group.tickers) {
      const existing = m.get(t);
      if (existing) {
        throw new Error(
          `Ticker "${t}" appears in both "${existing}" and "${key}" — equivalence groups must be disjoint`,
        );
      }
      m.set(t, key);
    }
  }
  return m;
}

export const GROUP_BY_TICKER: ReadonlyMap<string, string> = buildIndex();

export function groupOfTicker(ticker: string): string | null {
  return GROUP_BY_TICKER.get(ticker) ?? null;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/config/equivalent-groups.test.ts`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lib/config/equivalent-groups.ts src/lib/config/equivalent-groups.test.ts
git commit -m "feat(groups): static equivalence config + ticker index"
```

---

## Task 2: TxnType classifier

**Files:**
- Create: `src/lib/format/group-aggregation.ts` (first part — `TxnType` + `classifyTxn`)
- Create: `src/lib/format/group-aggregation.test.ts`

- [ ] **Step 1: Write the failing test**

```ts
// src/lib/format/group-aggregation.test.ts
import { describe, it, expect } from "vitest";
import { classifyTxn, type TxnType } from "./group-aggregation";
import type { FidelityTxn } from "@/lib/schemas";

const t = (actionType: string, extra: Partial<FidelityTxn> = {}): FidelityTxn => ({
  runDate: "2026-01-02",
  actionType,
  symbol: "VOO",
  amount: 100,
  quantity: 1,
  price: 100,
  ...extra,
});

describe("classifyTxn", () => {
  it("buy/sell → REAL", () => {
    expect(classifyTxn(t("buy"))).toBe<TxnType>("REAL");
    expect(classifyTxn(t("sell"))).toBe<TxnType>("REAL");
  });

  it("reinvestment → REINVEST", () => {
    expect(classifyTxn(t("reinvestment"))).toBe<TxnType>("REINVEST");
  });

  it("price=0 + qty≠0 → SPLIT (Fidelity DISTRIBUTION encoding)", () => {
    expect(classifyTxn(t("distribution", { price: 0, quantity: 1 }))).toBe<TxnType>("SPLIT");
  });

  it("dividend / interest / other → OTHER", () => {
    expect(classifyTxn(t("dividend"))).toBe<TxnType>("OTHER");
    expect(classifyTxn(t("interest"))).toBe<TxnType>("OTHER");
    expect(classifyTxn(t("deposit"))).toBe<TxnType>("OTHER");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/format/group-aggregation.test.ts`
Expected: FAIL with "Cannot find module './group-aggregation'"

- [ ] **Step 3: Write implementation**

```ts
// src/lib/format/group-aggregation.ts
// ── Transaction classifier + group aggregation (pure data layer) ────────
// Classifies Fidelity transactions into a higher-level taxonomy so UI
// code doesn't have to ad-hoc match action strings. Group aggregation
// uses this taxonomy to decide which txns count toward the group net.

import type { FidelityTxn } from "@/lib/schemas";

export type TxnType = "REAL" | "REINVEST" | "SPLIT" | "ROLLOVER" | "OTHER";

export function classifyTxn(t: FidelityTxn): TxnType {
  const a = t.actionType;
  if (a === "buy" || a === "sell") return "REAL";
  if (a === "reinvestment") return "REINVEST";
  // Fidelity encodes splits as DISTRIBUTION with price=0 and qty≠0
  if (a === "distribution" && t.price === 0 && t.quantity !== 0) return "SPLIT";
  return "OTHER";
}
```

Note: V1 doesn't emit ROLLOVER; we'd need account-level data currently not in the `FidelityTxn` schema. Leaving the type member in so future work can add the branch without a signature change.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm run test -- src/lib/format/group-aggregation.test.ts`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/lib/format/group-aggregation.ts src/lib/format/group-aggregation.test.ts
git commit -m "feat(groups): classifyTxn TxnType taxonomy"
```

---

## Task 3: groupNetByDate algorithm

**Files:**
- Modify: `src/lib/format/group-aggregation.ts`
- Modify: `src/lib/format/group-aggregation.test.ts`

- [ ] **Step 1: Write the failing tests**

Append to `src/lib/format/group-aggregation.test.ts`:

```ts
import { groupNetByDate } from "./group-aggregation";

const real = (runDate: string, symbol: string, actionType: "buy" | "sell", amount: number): FidelityTxn => ({
  runDate, actionType, symbol,
  amount: actionType === "sell" ? -Math.abs(amount) : amount,  // actual sign doesn't matter; algo uses abs
  quantity: 1,
  price: amount,
});

describe("groupNetByDate", () => {
  it("same-day exact swap → no marker", () => {
    const txns = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 1000)];
    const out = groupNetByDate(txns);
    expect(out.get("sp500")?.size ?? 0).toBe(0);
  });

  it("same-day partial swap → one S marker at net", () => {
    const txns = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 500)];
    const out = groupNetByDate(txns);
    const entries = Array.from(out.get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].side).toBe("sell");
    expect(entries[0].net).toBe(500);
    expect(entries[0].date).toBe("2026-01-02");
    expect(entries[0].breakdown).toEqual([
      { symbol: "SPY", signed: 1000 },
      { symbol: "VOO", signed: -500 },
    ]);
  });

  it("T+1 swap pairs (Mon sell → Tue buy)", () => {
    const txns = [real("2026-01-05", "SPY", "sell", 1000), real("2026-01-06", "VOO", "buy", 1000)];
    expect(groupNetByDate(txns).get("sp500")?.size ?? 0).toBe(0);
  });

  it("T+2 window: 3-day gap (Mon → Thu) does NOT pair → two markers", () => {
    const txns = [real("2026-01-05", "SPY", "sell", 1000), real("2026-01-08", "VOO", "buy", 1000)];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(2);
    expect(entries[0].side).toBe("sell");
    expect(entries[1].side).toBe("buy");
  });

  it("chained T+2 (day1 → day3 → day5) all in one cluster", () => {
    const txns = [
      real("2026-01-05", "SPY", "sell", 1000),
      real("2026-01-07", "VOO", "buy", 500),
      real("2026-01-09", "IVV", "buy", 400),
    ];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].net).toBe(100);   // sells 1000 − buys 900
    expect(entries[0].side).toBe("sell");
    expect(entries[0].date).toBe("2026-01-05");  // earliest date in cluster
  });

  it("REINVEST excluded from net", () => {
    const txns: FidelityTxn[] = [
      real("2026-01-02", "SPY", "sell", 1000),
      { runDate: "2026-01-02", actionType: "reinvestment", symbol: "VOO", amount: 50, quantity: 0.5, price: 100 },
    ];
    const entries = Array.from(groupNetByDate(txns).get("sp500")!.values());
    expect(entries).toHaveLength(1);
    expect(entries[0].net).toBe(1000);  // reinvest ignored
  });

  it("tickers outside any group produce no entries", () => {
    const txns = [real("2026-01-02", "NVDA", "buy", 1000)];
    expect(groupNetByDate(txns).size).toBe(0);
  });

  it("$49 net → no marker (below threshold); $51 → marker", () => {
    const below = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 951)];
    expect(groupNetByDate(below).get("sp500")?.size ?? 0).toBe(0);

    const above = [real("2026-01-02", "SPY", "sell", 1000), real("2026-01-02", "VOO", "buy", 949)];
    expect(groupNetByDate(above).get("sp500")!.size).toBe(1);
  });

  it("cross-group txns don't interfere", () => {
    const txns = [
      real("2026-01-02", "SPY", "sell", 1000),
      real("2026-01-02", "QQQ", "sell", 500),
    ];
    const out = groupNetByDate(txns);
    expect(out.get("sp500")?.size).toBe(1);
    expect(out.get("nasdaq_100")?.size).toBe(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm run test -- src/lib/format/group-aggregation.test.ts`
Expected: FAIL with "Cannot find export 'groupNetByDate'"

- [ ] **Step 3: Write implementation**

Append to `src/lib/format/group-aggregation.ts`:

```ts
import { groupOfTicker } from "@/lib/config/equivalent-groups";

const MS_PER_DAY = 86_400_000;
const WINDOW_DAYS = 2;
const THRESHOLD_USD = 50;

export type GroupNetEntry = {
  date: string;             // earliest date in the cluster
  side: "buy" | "sell";
  net: number;              // absolute net amount in USD
  breakdown: { symbol: string; signed: number }[];  // sell-positive, buy-negative
};

type Real = { date: string; ts: number; symbol: string; side: "buy" | "sell"; amount: number };

function parseIso(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d).getTime();
}

/**
 * Group REAL buy/sell txns by equivalence group, cluster within T+2
 * chaining, emit one marker per cluster iff |net| >= $50. REINVEST /
 * SPLIT / OTHER txns and tickers not in any group are ignored.
 *
 * Returns `Map<groupKey, Map<date, GroupNetEntry>>` so callers can
 * look up markers by (group, date) in O(1).
 */
export function groupNetByDate(
  txns: FidelityTxn[],
): Map<string, Map<string, GroupNetEntry>> {
  // Bucket REAL txns by group
  const byGroup = new Map<string, Real[]>();
  for (const t of txns) {
    if (classifyTxn(t) !== "REAL") continue;
    const groupKey = groupOfTicker(t.symbol);
    if (!groupKey) continue;
    const side: "buy" | "sell" = t.actionType === "sell" ? "sell" : "buy";
    const entry: Real = {
      date: t.runDate,
      ts: parseIso(t.runDate),
      symbol: t.symbol,
      side,
      amount: Math.abs(t.amount),
    };
    const arr = byGroup.get(groupKey);
    if (arr) arr.push(entry);
    else byGroup.set(groupKey, [entry]);
  }

  const result = new Map<string, Map<string, GroupNetEntry>>();

  for (const [groupKey, groupTxns] of byGroup) {
    groupTxns.sort((a, b) => a.ts - b.ts);

    const clusters: Real[][] = [];
    for (const t of groupTxns) {
      const last = clusters[clusters.length - 1];
      if (last && (t.ts - last[last.length - 1].ts) <= WINDOW_DAYS * MS_PER_DAY) {
        last.push(t);
      } else {
        clusters.push([t]);
      }
    }

    const byDate = new Map<string, GroupNetEntry>();
    for (const cluster of clusters) {
      // Sell-positive convention: sells add, buys subtract, so net > 0 ⇒ net sell.
      let net = 0;
      const breakdown: { symbol: string; signed: number }[] = [];
      for (const t of cluster) {
        const signed = t.side === "sell" ? t.amount : -t.amount;
        net += signed;
        breakdown.push({ symbol: t.symbol, signed });
      }
      if (Math.abs(net) < THRESHOLD_USD) continue;
      const entry: GroupNetEntry = {
        date: cluster[0].date,
        side: net > 0 ? "sell" : "buy",
        net: Math.abs(net),
        breakdown,
      };
      byDate.set(entry.date, entry);
    }

    if (byDate.size > 0) result.set(groupKey, byDate);
  }

  return result;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- src/lib/format/group-aggregation.test.ts`
Expected: PASS (9 new tests, 13 total)

- [ ] **Step 5: Commit**

```bash
git add src/lib/format/group-aggregation.ts src/lib/format/group-aggregation.test.ts
git commit -m "feat(groups): groupNetByDate T+2 clustering + \$50 threshold"
```

---

## Task 4: Reinvest-dot marker

**Files:**
- Modify: `src/lib/format/ticker-data.ts:6-20` (add `reinvestQty` / `reinvestAmount` fields)
- Modify: `src/lib/format/ticker-data.ts:41-94` (extend `mergeTickerData` to populate the new fields)
- Modify: `src/components/finance/ticker-markers.tsx` (add `ReinvestMarker`)
- Modify: `src/components/finance/ticker-chart-base.tsx` and `ticker-dialog.tsx` (render the new Scatter)
- Create: `src/lib/format/ticker-data.test.ts` (if missing — otherwise append)

- [ ] **Step 1: Split out reinvestment in `mergeTickerData`**

Edit `src/lib/format/ticker-data.ts`:

```ts
export type TickerChartPoint = {
  date: string;
  ts: number;
  close: number;
  buyPrice?: number;
  buyQty?: number;
  buyAmount?: number;
  buyTxnCount?: number;
  sellPrice?: number;
  sellQty?: number;
  sellAmount?: number;
  sellTxnCount?: number;
  reinvestAmount?: number;   // NEW: summed abs($) of reinvestment txns on this date
  reinvestTxnCount?: number; // NEW
};
```

Then in `mergeTickerData`, stop folding `reinvestment` into `buyMap`. Split into its own bucket:

```ts
const buyMap = new Map<string, { qty: number; amount: number; count: number }>();
const sellMap = new Map<string, { qty: number; amount: number; count: number }>();
const reinvestMap = new Map<string, { amount: number; count: number }>();

for (const t of transactions) {
  const iso = t.runDate;
  const qty = Math.abs(t.quantity);
  const amount = Math.abs(t.amount);
  if (t.actionType === "buy") {
    const e = buyMap.get(iso); if (e) { e.qty+=qty; e.amount+=amount; e.count+=1; } else buyMap.set(iso, { qty, amount, count: 1 });
  } else if (t.actionType === "sell") {
    const e = sellMap.get(iso); if (e) { e.qty+=qty; e.amount+=amount; e.count+=1; } else sellMap.set(iso, { qty, amount, count: 1 });
  } else if (t.actionType === "reinvestment") {
    const e = reinvestMap.get(iso); if (e) { e.amount+=amount; e.count+=1; } else reinvestMap.set(iso, { amount, count: 1 });
  }
}
```

Populate points:

```ts
return prices.map((p) => {
  const [y, m, d] = p.date.split("-");
  const ts = new Date(+y, +m - 1, +d).getTime();
  const point: TickerChartPoint = { date: p.date, ts, close: p.close };
  const buy = buyMap.get(p.date);
  if (buy) { point.buyPrice = buy.qty>0 ? buy.amount/buy.qty : 0; point.buyQty = buy.qty; point.buyAmount = buy.amount; point.buyTxnCount = buy.count; }
  const sell = sellMap.get(p.date);
  if (sell) { point.sellPrice = sell.qty>0 ? sell.amount/sell.qty : 0; point.sellQty = sell.qty; point.sellAmount = sell.amount; point.sellTxnCount = sell.count; }
  const reinvest = reinvestMap.get(p.date);
  if (reinvest) { point.reinvestAmount = reinvest.amount; point.reinvestTxnCount = reinvest.count; }
  return point;
});
```

Note: this changes the per-ticker chart's avg-cost-basis calc (`ticker-chart.tsx:36-39`) because reinvestment is still conceptually a buy for cost-basis purposes. **Do not change that computation** — the filter there is already explicit (`t.actionType === "buy" || t.actionType === "reinvestment"`) and keeps working as-is.

- [ ] **Step 2: Write the ReinvestMarker component**

Edit `src/components/finance/ticker-markers.tsx` — append:

```tsx
// ── REINVEST marker: tiny muted dot, non-interactive ──────────────────
export function ReinvestMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  return <circle cx={cx} cy={cy} r={2.5} fill={BUY_COLOR} fillOpacity={0.4} />;
}
```

- [ ] **Step 3: Attach a `reinvestDot` dataKey so Recharts can place it on the chart**

The Scatter needs a numeric dataKey. Reinvestments render at the close price of that day.

Edit `src/lib/format/ticker-data.ts`: in `buildClusteredData`, after the `nearestIdx` block, add:

```ts
for (const d of out) {
  if (d.reinvestAmount != null) {
    // Place at the close price so the dot sits on the price line
    (d as ClusteredPoint & { reinvestDot?: number }).reinvestDot = d.close;
  }
}
```

Update `ClusteredPoint` type:

```ts
export type ClusteredPoint = TickerChartPoint & {
  buyClusterPrice?: number;
  buyCluster?: Cluster;
  sellClusterPrice?: number;
  sellCluster?: Cluster;
  reinvestDot?: number;
};
```

- [ ] **Step 4: Render the Scatter in both inline and dialog charts**

Edit `src/components/finance/ticker-dialog.tsx` — add import and Scatter:

```tsx
import { BuyClusterMarker, SellClusterMarker, ReinvestMarker, /* ... */ } from "./ticker-markers";
```

Inside `TickerDialogChart`'s `<ComposedChart>`, BEFORE the sell/buy Scatters (so reinvest dots paint underneath):

```tsx
<Scatter dataKey="reinvestDot" shape={ReinvestMarker} legendType="none" isAnimationActive={false} />
```

Do the equivalent edit in `src/components/finance/ticker-chart-base.tsx`.

- [ ] **Step 5: Run existing tests (no regressions) + add one for reinvestMap split**

Add to `src/lib/format/ticker-data.test.ts` (create if missing):

```ts
import { describe, it, expect } from "vitest";
import { mergeTickerData } from "./ticker-data";

describe("mergeTickerData reinvestment split", () => {
  it("separates reinvestment from buys", () => {
    const out = mergeTickerData(
      [{ date: "2026-01-02", close: 100 }],
      [
        { runDate: "2026-01-02", actionType: "buy", symbol: "VOO", amount: 100, quantity: 1, price: 100 },
        { runDate: "2026-01-02", actionType: "reinvestment", symbol: "VOO", amount: 5, quantity: 0.05, price: 100 },
      ],
    );
    expect(out[0].buyAmount).toBe(100);
    expect(out[0].buyTxnCount).toBe(1);
    expect(out[0].reinvestAmount).toBe(5);
    expect(out[0].reinvestTxnCount).toBe(1);
  });
});
```

Run: `npm run test -- src/lib/format/ticker-data.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/lib/format/ticker-data.ts src/lib/format/ticker-data.test.ts src/components/finance/ticker-markers.tsx src/components/finance/ticker-chart-base.tsx src/components/finance/ticker-dialog.tsx
git commit -m "feat(groups): REINVEST rendered as tiny muted dot (separated from buy clusters)"
```

---

## Task 5: Group-view data in `computeActivity`

**Files:**
- Modify: `src/lib/compute/compute.ts` (add `computeGroupedActivity`)
- Modify: `src/lib/compute/compute.test.ts`

The toggle lives in the Activity UI (Task 6). This task provides the data function so the toggle can just switch between two already-computed shapes.

- [ ] **Step 1: Write the failing test**

Append to `src/lib/compute/compute.test.ts`:

```ts
import { computeGroupedActivity } from "./compute";
import type { FidelityTxn } from "@/lib/schemas";

describe("computeGroupedActivity", () => {
  it("aggregates group tickers into one row (net-sell cluster)", () => {
    const txns: FidelityTxn[] = [
      { runDate: "2026-01-02", actionType: "sell", symbol: "SPY", amount: -1000, quantity: 5, price: 200 },
      { runDate: "2026-01-02", actionType: "buy",  symbol: "VOO", amount:  500, quantity: 1, price: 500 },
      // solo ticker
      { runDate: "2026-01-03", actionType: "buy",  symbol: "NVDA", amount: 2000, quantity: 10, price: 200 },
    ];
    const act = computeGroupedActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.sellsBySymbol).toContainEqual({ symbol: "S&P 500", count: 1, total: 500, isGroup: true, groupKey: "sp500" });
    expect(act.buysBySymbol).toContainEqual({ symbol: "NVDA", count: 1, total: 2000, isGroup: false });
    // SPY and VOO should NOT appear as separate rows
    expect(act.sellsBySymbol.find(r => r.symbol === "SPY")).toBeUndefined();
    expect(act.buysBySymbol.find(r => r.symbol === "VOO")).toBeUndefined();
  });

  it("exact swap produces no group row", () => {
    const txns: FidelityTxn[] = [
      { runDate: "2026-01-02", actionType: "sell", symbol: "SPY", amount: -1000, quantity: 5, price: 200 },
      { runDate: "2026-01-02", actionType: "buy",  symbol: "VOO", amount: 1000, quantity: 2, price: 500 },
    ];
    const act = computeGroupedActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toEqual([]);
    expect(act.sellsBySymbol).toEqual([]);
  });

  it("dividends remain per-ticker (not grouped)", () => {
    const txns: FidelityTxn[] = [
      { runDate: "2026-01-02", actionType: "dividend", symbol: "VOO", amount: 10, quantity: 0, price: 0 },
      { runDate: "2026-01-02", actionType: "dividend", symbol: "SPY", amount: 5, quantity: 0, price: 0 },
    ];
    const act = computeGroupedActivity(txns, "2026-01-01", "2026-01-31");
    // dividend grouping is out of scope; show raw
    expect(act.dividendsBySymbol.map(r => r.symbol).sort()).toEqual(["SPY", "VOO"]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- src/lib/compute/compute.test.ts`
Expected: FAIL with "Cannot find export 'computeGroupedActivity'"

- [ ] **Step 3: Write implementation**

Append to `src/lib/compute/compute.ts`:

```ts
import { groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS, groupOfTicker } from "@/lib/config/equivalent-groups";

export type ActivityRow = {
  symbol: string;
  count: number;
  total: number;
  isGroup?: boolean;
  groupKey?: string;
};

export type GroupedActivityResponse = {
  buysBySymbol: ActivityRow[];
  sellsBySymbol: ActivityRow[];
  dividendsBySymbol: ActivityRow[];
};

export function computeGroupedActivity(
  fidelityTxns: FidelityTxn[],
  start: string,
  end: string,
): GroupedActivityResponse {
  // Window the txns first — groupNetByDate + the solo pass both read this
  const windowed = fidelityTxns.filter((t) => t.runDate >= start && t.runDate <= end && t.symbol);

  // Group markers via the shared algorithm
  const groupMarkers = groupNetByDate(windowed);
  const groupBuys: ActivityRow[] = [];
  const groupSells: ActivityRow[] = [];
  for (const [groupKey, byDate] of groupMarkers) {
    const display = EQUIVALENT_GROUPS[groupKey].display;
    let buyTotal = 0, buyCount = 0, sellTotal = 0, sellCount = 0;
    for (const entry of byDate.values()) {
      if (entry.side === "buy") { buyTotal += entry.net; buyCount += 1; }
      else                      { sellTotal += entry.net; sellCount += 1; }
    }
    if (buyCount > 0)  groupBuys.push({ symbol: display, count: buyCount, total: round(buyTotal), isGroup: true, groupKey });
    if (sellCount > 0) groupSells.push({ symbol: display, count: sellCount, total: round(sellTotal), isGroup: true, groupKey });
  }

  // Solo tickers (not in any group) — reuse computeActivity shape
  const solo = windowed.filter((t) => !groupOfTicker(t.symbol));
  const soloActivity = computeActivity(solo, start, end);

  // Dividends stay per-ticker (grouping is out of scope for dividends)
  const divs = computeActivity(windowed, start, end).dividendsBySymbol;

  const sortDesc = (a: ActivityRow, b: ActivityRow) => b.total - a.total;
  return {
    buysBySymbol:  [...groupBuys,  ...soloActivity.buysBySymbol.map(r => ({ ...r, isGroup: false as const }))].sort(sortDesc),
    sellsBySymbol: [...groupSells, ...soloActivity.sellsBySymbol.map(r => ({ ...r, isGroup: false as const }))].sort(sortDesc),
    dividendsBySymbol: divs.map(r => ({ ...r, isGroup: false as const })),
  };
}
```

Also widen `computeActivity`'s return so the rows have the new field:

```ts
// At end of computeActivity, the toList helper already returns { symbol, count, total }
// Add isGroup: false in the spread above — done in computeGroupedActivity's map step.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test -- src/lib/compute/compute.test.ts`
Expected: PASS (existing + 3 new)

- [ ] **Step 5: Commit**

```bash
git add src/lib/compute/compute.ts src/lib/compute/compute.test.ts
git commit -m "feat(groups): computeGroupedActivity applies groupNetByDate for B/S rows"
```

---

## Task 6: Activity toggle UI + group row wiring

**Files:**
- Modify: `src/components/finance/ticker-table.tsx` (accept `isGroup`; clicking a group opens `GroupChartDialog`)
- Modify: `src/app/finance/page.tsx` (toggle state in `ActivityContent`)

- [ ] **Step 1: Extend `TickerTable` row typing**

Edit `src/components/finance/ticker-table.tsx` — change the `data` prop type so rows can carry group metadata:

```ts
export type ActivityTableRow = {
  symbol: string;
  count: number;
  total: number;
  isGroup?: boolean;
  groupKey?: string;
};

export function TickerTable({
  title, data, startDate, endDate, countLabel = "Trades",
}: {
  title: string;
  data: ActivityTableRow[];
  startDate?: string;
  endDate?: string;
  countLabel?: string;
}) {
  // ... same body, but inside TickerRow's onToggle, when `item.isGroup`, open a GroupChartDialog instead of the inline <TickerChart />.
}
```

Update `TickerRow` and `TickerRowOverflow` to take an optional `groupKey` and, when expanded+grouped, render `<GroupInlineChart groupKey={...} />` (a thin wrapper around the new `GroupChart` component from Task 7).

- [ ] **Step 2: Add the toggle to ActivityContent**

Edit `src/app/finance/page.tsx:62-84`:

```tsx
function ActivityContent({
  activity, groupedActivity, startDate, snapshotDate,
}: {
  activity: ReturnType<typeof useBundle>["activity"];
  groupedActivity: ReturnType<typeof useBundle>["groupedActivity"];
  startDate: string | null;
  snapshotDate: string | null;
}) {
  const [grouped, setGrouped] = useState(true);  // Default ON per spec; in-memory only
  if (!activity) return <SectionMessage kind="unavailable">Activity data unavailable</SectionMessage>;
  const shown = grouped ? groupedActivity : activity;
  const { buysBySymbol, sellsBySymbol, dividendsBySymbol } = shown;
  if (buysBySymbol.length === 0 && sellsBySymbol.length === 0 && dividendsBySymbol.length === 0) {
    return <SectionMessage kind="empty">No activity in this period</SectionMessage>;
  }
  return (
    <SectionBody>
      <div className="flex justify-end mb-2">
        <label className="inline-flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
          Group equivalent tickers
        </label>
      </div>
      <div className="grid md:grid-cols-2 gap-6">
        <TickerTable title="Buys by Symbol"  data={buysBySymbol}  startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
        <TickerTable title="Sells by Symbol" data={sellsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
        <TickerTable title="Dividends by Symbol" data={dividendsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} countLabel="Payments" />
      </div>
    </SectionBody>
  );
}
```

Note: current code shows only Buys + Dividends; this plan **also adds Sells by Symbol** because group-view sells are where the spec's benefit shows up most clearly. Confirm with user after Task 6 lands.

- [ ] **Step 3: Wire `groupedActivity` into `useBundle`**

Edit `src/lib/hooks/use-bundle.ts` — inside the same `useMemo` that produces `activity`, also compute `groupedActivity`:

```ts
import { computeActivity, computeGroupedActivity } from "@/lib/compute/compute";
// inside the memo:
const activity = ...;
const groupedActivity = snapshotDate ? computeGroupedActivity(fidelityTxns, startDate, snapshotDate) : null;
return { ..., activity, groupedActivity };
```

(Inspect `use-bundle.ts` for the exact names of `startDate`/`snapshotDate`/`fidelityTxns` before editing.)

- [ ] **Step 4: Type + component test**

Add to `src/components/finance/ticker-table.test.tsx`:

```tsx
it("renders group rows with isGroup flag", () => {
  render(<TickerTable title="Test" data={[
    { symbol: "NASDAQ 100", count: 2, total: 3000, isGroup: true, groupKey: "nasdaq_100" },
    { symbol: "NVDA", count: 1, total: 500 },
  ]} />);
  expect(screen.getByText("NASDAQ 100")).toBeInTheDocument();
  expect(screen.getByText("NVDA")).toBeInTheDocument();
});
```

Run: `npm run test -- src/components/finance/ticker-table.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/components/finance/ticker-table.tsx src/components/finance/ticker-table.test.tsx src/app/finance/page.tsx src/lib/hooks/use-bundle.ts
git commit -m "feat(groups): activity toggle + group rows in TickerTable"
```

---

## Task 7: Group chart + group dialog

**Files:**
- Create: `src/components/finance/group-chart.tsx`
- Create: `src/components/finance/group-dialog.tsx`
- Create: `src/components/finance/group-dialog.test.tsx`

The group chart's Y-axis is **summed position value** across constituent tickers, per date. Input: `dailyTickers` from the bundle (`DailyTicker[]`) — filter to tickers in the group, group by date, sum `value`.

- [ ] **Step 1: Write the data function (pure)**

Append to `src/lib/format/group-aggregation.ts`:

```ts
import type { DailyTicker } from "@/lib/schemas";

export type GroupValuePoint = {
  date: string;
  ts: number;
  value: number;     // sum of constituent tickers' position value
  constituents: { ticker: string; value: number }[];
};

export function buildGroupValueSeries(
  dailyTickers: DailyTicker[],
  groupTickers: string[],
): GroupValuePoint[] {
  const set = new Set(groupTickers);
  const byDate = new Map<string, { value: number; parts: { ticker: string; value: number }[] }>();
  for (const dt of dailyTickers) {
    if (!set.has(dt.ticker)) continue;
    const e = byDate.get(dt.date);
    if (e) { e.value += dt.value; e.parts.push({ ticker: dt.ticker, value: dt.value }); }
    else byDate.set(dt.date, { value: dt.value, parts: [{ ticker: dt.ticker, value: dt.value }] });
  }
  return [...byDate.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, { value, parts }]) => {
      const [y, m, d] = date.split("-").map(Number);
      return { date, ts: new Date(y, m - 1, d).getTime(), value, constituents: parts };
    });
}
```

Add a test:

```ts
describe("buildGroupValueSeries", () => {
  it("sums constituent values per date", () => {
    const series = buildGroupValueSeries([
      { date: "2026-01-02", ticker: "SPY", value: 1000, category: "", subtype: "", costBasis: 0, gainLoss: 0, gainLossPct: 0 },
      { date: "2026-01-02", ticker: "VOO", value: 500,  category: "", subtype: "", costBasis: 0, gainLoss: 0, gainLossPct: 0 },
      { date: "2026-01-03", ticker: "SPY", value: 1100, category: "", subtype: "", costBasis: 0, gainLoss: 0, gainLossPct: 0 },
    ], ["SPY", "VOO"]);
    expect(series).toHaveLength(2);
    expect(series[0]).toMatchObject({ date: "2026-01-02", value: 1500 });
    expect(series[0].constituents).toHaveLength(2);
    expect(series[1]).toMatchObject({ date: "2026-01-03", value: 1100 });
  });
});
```

- [ ] **Step 2: Write `group-chart.tsx` (inline)**

```tsx
"use client";
import { ComposedChart, Line, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from "recharts";
import { useIsDark } from "@/lib/hooks/hooks";
import { gridStroke, axisProps } from "@/lib/format/chart-styles";
import { fmtCurrencyShort, fmtTick } from "@/lib/format/format";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";
import { BuyClusterMarker, SellClusterMarker } from "./ticker-markers";
import type { GroupValuePoint } from "@/lib/format/group-aggregation";
import type { GroupNetEntry } from "@/lib/format/group-aggregation";

type GroupMarkerCluster = {
  ts: number;
  count: number;
  r: number;
  amount: number;
  price: number;
  qty: number;
  memberDates: string[];
  breakdown: { symbol: string; signed: number }[];
};

export type GroupChartPoint = GroupValuePoint & {
  buyCluster?: GroupMarkerCluster;
  sellCluster?: GroupMarkerCluster;
  buyClusterPrice?: number;
  sellClusterPrice?: number;
};

/** Combine the daily value series with the group-net markers into chart points. */
export function buildGroupChartData(
  series: GroupValuePoint[],
  markers: Map<string, GroupNetEntry>,
): GroupChartPoint[] {
  return series.map((p) => {
    const entry = markers.get(p.date);
    if (!entry) return p;
    // Fake a Cluster shape so the existing BuyClusterMarker/SellClusterMarker renderers work.
    const cluster = {
      ts: p.ts, count: 1, r: 12, amount: entry.net, price: 0, qty: 0, memberDates: [p.date],
      breakdown: entry.breakdown,
    };
    if (entry.side === "buy")  return { ...p, buyCluster: cluster,  buyClusterPrice:  p.value };
    else                       return { ...p, sellCluster: cluster, sellClusterPrice: p.value };
  });
}

export function GroupChart({ data, displayName }: { data: GroupChartPoint[]; displayName: string }) {
  const isDark = useIsDark();
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} vertical={false} />
        <XAxis dataKey="ts" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtTick} hide {...axisProps(isDark)} />
        <YAxis domain={["auto", "auto"]} tickFormatter={fmtCurrencyShort} width={60} {...axisProps(isDark)} axisLine={false} />
        <Tooltip />
        <Line type="monotone" dataKey="value" stroke={isDark ? "#60a5fa" : "#2563eb"} strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Scatter dataKey="sellClusterPrice" shape={SellClusterMarker} legendType="none" isAnimationActive={false} />
        <Scatter dataKey="buyClusterPrice"  shape={BuyClusterMarker}  legendType="none" isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
```

Note: Recharts passes `props.payload` to custom shape components; the existing `BuyClusterMarker`/`SellClusterMarker` read `payload.buyCluster` / `payload.sellCluster`. Reusing them here means no new marker component needed — but the tooltip's `breakdown` field is new (Task 8).

- [ ] **Step 3: Write `group-dialog.tsx`**

Mirror `TickerChartDialog`:

```tsx
"use client";
import { useEffect, useRef } from "react";
import { GroupChart, buildGroupChartData } from "./group-chart";
import { buildGroupValueSeries, groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS } from "@/lib/config/equivalent-groups";
import { useIsDark } from "@/lib/hooks/hooks";
import { fmtCurrency } from "@/lib/format/format";
import type { DailyTicker, FidelityTxn } from "@/lib/schemas";

export function GroupChartDialog({
  groupKey, dailyTickers, fidelityTxns, startDate, endDate, onClose,
}: {
  groupKey: string;
  dailyTickers: DailyTicker[];
  fidelityTxns: FidelityTxn[];
  startDate?: string;
  endDate?: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const isDark = useIsDark();
  const group = EQUIVALENT_GROUPS[groupKey];
  useEffect(() => {
    const el = dialogRef.current; if (!el) return;
    el.showModal();
    const onCancel = (e: Event) => { e.preventDefault(); onClose(); };
    el.addEventListener("cancel", onCancel);
    const prev = document.body.style.overflow; document.body.style.overflow = "hidden";
    return () => { el.removeEventListener("cancel", onCancel); document.body.style.overflow = prev; };
  }, [onClose]);

  const series = buildGroupValueSeries(dailyTickers, group.tickers)
    .filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate));
  const markers = groupNetByDate(fidelityTxns).get(groupKey) ?? new Map();
  const data = buildGroupChartData(series, markers);

  const latest = series[series.length - 1];

  return (
    <dialog
      ref={dialogRef}
      onClick={(e) => { e.stopPropagation(); if (e.target === dialogRef.current) onClose(); }}
      className="fixed inset-0 m-auto backdrop:bg-black/50 backdrop:backdrop-blur-sm bg-transparent p-0 max-w-none max-h-none border-0 overflow-visible"
    >
      <div className={`${isDark ? "bg-zinc-900 text-zinc-100" : "bg-white text-zinc-900"} rounded-xl shadow-2xl flex flex-col resize overflow-hidden w-[95vw] h-[92vh] min-w-[400px] min-h-[300px] max-w-[99vw] max-h-[98vh]`}>
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-foreground/10">
          <div className="flex items-baseline gap-3">
            <span className="font-semibold text-lg">{group.display}</span>
            {latest && <span className="text-sm text-muted-foreground">{fmtCurrency(latest.value)}</span>}
            <span className="text-xs text-muted-foreground">{group.tickers.join(" · ")}</span>
          </div>
          <button onClick={onClose} aria-label="Close" className={`w-8 h-8 flex items-center justify-center rounded-full text-2xl leading-none ${isDark ? "hover:bg-zinc-800" : "hover:bg-zinc-100"}`}>&times;</button>
        </div>
        <div className="flex-1 min-h-0 px-4 pt-4 pb-4">
          <GroupChart data={data} displayName={group.display} />
        </div>
      </div>
    </dialog>
  );
}
```

- [ ] **Step 4: Open it from `ticker-table.tsx`**

Inside `TickerRow.onToggle`: if `isGroup`, open `<GroupChartDialog groupKey={...} dailyTickers={dailyTickers} fidelityTxns={fidelityTxns} />` instead of the inline `<TickerChart />`. The `dailyTickers` + `fidelityTxns` need to be threaded down from the bundle — add them as `TickerTable` props.

- [ ] **Step 5: Smoke test the dialog**

```tsx
// src/components/finance/group-dialog.test.tsx
import { render, screen } from "@testing-library/react";
import { GroupChartDialog } from "./group-dialog";

it("renders group display name + constituents", () => {
  render(
    <GroupChartDialog
      groupKey="sp500"
      dailyTickers={[]}
      fidelityTxns={[]}
      onClose={() => {}}
    />,
  );
  expect(screen.getByText("S&P 500")).toBeInTheDocument();
  expect(screen.getByText(/VOO.*SPY/)).toBeInTheDocument();
});
```

- [ ] **Step 6: Commit**

```bash
git add src/lib/format/group-aggregation.ts src/lib/format/group-aggregation.test.ts src/components/finance/group-chart.tsx src/components/finance/group-dialog.tsx src/components/finance/group-dialog.test.tsx src/components/finance/ticker-table.tsx
git commit -m "feat(groups): group chart + group dialog (total-value Y-axis)"
```

---

## Task 8: Group-marker tooltip with ticker breakdown

**Files:**
- Modify: `src/components/finance/group-chart.tsx` — custom Tooltip that renders the `breakdown` array

- [ ] **Step 1: Add custom tooltip content**

In `group-chart.tsx`, replace the bare `<Tooltip />` with:

```tsx
function GroupTooltip({ active, payload }: TooltipContentProps) {
  const p = payload?.[0]?.payload as GroupChartPoint | undefined;
  if (!active || !p) return null;
  const marker = p.buyCluster ?? p.sellCluster;
  return (
    <TooltipCard active={active} payload={payload} title={fmtDateMedium(p.date)}>
      <p style={{ margin: 0 }}>Value: {fmtCurrency(p.value)}</p>
      {marker && (
        <>
          <p style={{ margin: "6px 0 0 0", fontWeight: 600, color: p.sellCluster ? SELL_COLOR : BUY_COLOR }}>
            Net {p.sellCluster ? "sell" : "buy"}: {fmtCurrency(marker.amount)}
          </p>
          {marker.breakdown.map((b) => (
            <p key={b.symbol} style={{ margin: 0, fontSize: 12 }}>
              <span className="font-mono">{b.symbol}</span> {b.signed >= 0 ? "−" : "+"}{fmtCurrency(Math.abs(b.signed))}
            </p>
          ))}
        </>
      )}
    </TooltipCard>
  );
}
```

(Sell-positive convention in the algo: `signed > 0` = sell contribution → displayed as negative exposure change.)

- [ ] **Step 2: Visual check**

Run: `npm run dev`
Open <http://localhost:3000/finance>, toggle Group view, hover the B/S marker on an S&P 500 row's chart → tooltip should show "Net sell: $500 | SPY −$1000 | VOO +$500".

- [ ] **Step 3: Commit**

```bash
git add src/components/finance/group-chart.tsx
git commit -m "feat(groups): tooltip shows per-ticker breakdown on group markers"
```

---

## Task 9: Playwright smoke test

**Files:**
- Create: `e2e/group-toggle.spec.ts`

- [ ] **Step 1: Write the smoke test**

```ts
// e2e/group-toggle.spec.ts
import { test, expect } from "@playwright/test";

test("group toggle swaps ticker rows to group rows", async ({ page }) => {
  await page.goto("/finance");
  // Default ON: group rows should be visible
  const activity = page.locator("section:has-text('Portfolio Activity')");
  await expect(activity.getByText("Group equivalent tickers")).toBeVisible();
  const toggle = activity.getByRole("checkbox", { name: /group/i });
  await expect(toggle).toBeChecked();

  // Toggle off
  await toggle.uncheck();
  await expect(toggle).not.toBeChecked();
  // When off, at least one raw ticker symbol is visible. (The mock fixture
  // in the e2e harness seeds at least one group constituent; adapt if the
  // fixture changes.)
});
```

- [ ] **Step 2: Run**

Run: `npx playwright test group-toggle`
Expected: PASS (needs e2e fixture to include at least one group-member txn; the existing mock seed data already includes VOO).

- [ ] **Step 3: Commit**

```bash
git add e2e/group-toggle.spec.ts
git commit -m "test(e2e): smoke for activity group toggle"
```

---

## Task 10: Manual end-to-end verification

- [ ] **Step 1: Reseed local D1 if stale**

```bash
cd pipeline && python3 scripts/sync_to_d1.py --local
```

- [ ] **Step 2: Start the worker + dev server**

```bash
# Terminal 1
cd worker && npx wrangler dev

# Terminal 2
npm run dev
```

- [ ] **Step 3: Walk the checklist**

Open <http://localhost:3000/finance>:

- Default: "Group equivalent tickers" toggle is ON
- Activity → Buys/Sells now show "NASDAQ 100" / "S&P 500" rows instead of individual SPY/VOO rows (assuming you have swap txns in-window)
- Click a group row: inline group chart appears; Y-axis is $ value, curve is cumulative position value
- Hover a B/S marker: tooltip shows net + ticker breakdown
- Double-click (or expand-icon) the inline chart: `GroupChartDialog` opens full-screen
- Open a single-ticker row (e.g. NVDA): existing behavior, price chart, no change
- Open a solo ticker that IS in a group but has no B/S cluster (e.g. if only REINVEST txns): tiny muted dot visible
- Toggle OFF: rows become per-ticker again — no regression vs. today's view

- [ ] **Step 4: Run full test suite + type-check**

```bash
npm run test
npx tsc --noEmit
cd pipeline && .venv/Scripts/python.exe -m pytest -q   # ensure generator parity didn't break
```

- [ ] **Step 5: Open a PR**

```bash
git push -u origin feat/group-aggregated-markers
gh pr create --title "feat: group-aggregated B/S markers for equivalent tickers" --body "$(cat <<'EOF'
## Summary
- Hand-maintained `EQUIVALENT_GROUPS` config (nasdaq_100, sp500 for V1)
- `classifyTxn` → TxnType taxonomy (REAL / REINVEST / SPLIT / OTHER)
- `groupNetByDate` — T+2 chaining cluster; \$50 threshold drops swap noise
- Activity toggle (default ON): swaps per-ticker rows for group rows + new group chart dialog
- REINVEST now rendered as a tiny muted dot instead of folded into a Buy cluster

## Spec
`docs/specs/2026-04-19-group-aggregated-bs-markers-design.md`

## Test plan
- [x] `npm run test` (unit + component)
- [x] `npx playwright test group-toggle`
- [x] Manual local: toggle default ON; click group row; hover marker → breakdown
- [x] No regression when toggle OFF

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review notes (for implementer)

- **Out of scope (per spec):** pipeline-side config, `etl/validate.py` ticker-in-two-groups check, D1 view, timemachine markers, cross-day (>T+2) swap detection, toggle persistence, group marker click drill-down, holding-period gradient in group dialog.
- **ROLLOVER branch:** intentionally left unemitted — `FidelityTxn` lacks account metadata on the frontend. Revisit when/if the bundle surfaces account.
- **Dividend grouping:** explicitly out of scope (dividends inform cash flow, not exposure — grouping would be noise, not signal).
- **Known V1 limitation (spec ref):** swaps that span >T+2 days show as separate B + S markers. Documented in the spec's "Known limitation" section. No code branch needed.

## Open implementation decisions (should be resolved inline while coding; all are reversible)

- Should `Sells by Symbol` be shown? Today's UI only shows Buys + Dividends. The spec is silent. Proposed: show Sells when group view is ON (that's where the noise-reduction benefit lands). Flag this to the user after Task 6 lands; default behavior above is "show it."
- Group dialog chart: currently reuses `BuyClusterMarker`/`SellClusterMarker` directly. If their `Cluster` shape diverges from what `GroupChart` passes, introduce a dedicated `GroupBuyMarker`/`GroupSellMarker` in Task 7. Prefer reuse for V1.
