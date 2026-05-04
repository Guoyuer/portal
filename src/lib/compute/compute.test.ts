import { describe, it, expect } from "vitest";
import type { CategoryMeta, DailyTicker, QianjiTxn } from "@/lib/schemas/timeline";
import {
  computeAllocation,
  computeCashflow,
  computeActivity,
  computeCrossCheck,
  computeGroupedActivity,
  computeMonthlyFlows,
  buildDateIndex,
  buildTickerIndex,
  catColorByName,
  normalizeInvestmentTxns,
  type InvestmentTxn,
} from "@/lib/compute/compute";
import type { ApiTicker } from "@/lib/compute/computed-types";
import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";
import { CATEGORIES, mkDaily, mkDailyN, mkFidelityTxn, mkQianjiTxn, mkRobinhoodTxn, mkEmpowerContribution, mkInvestmentTxn } from "@/test/factories";

const JAN_START = "2026-01-01";
const JAN_END = "2026-01-31";
const JAN_15 = "2026-01-15";

type AllocationFixture = {
  daily?: Parameters<typeof computeAllocation>[0];
  tickerIdx?: Map<string, ApiTicker[]>;
  date?: string;
  categories?: CategoryMeta[];
};

function computeDefaultAllocation({
  daily = [mkDaily()],
  tickerIdx = new Map<string, ApiTicker[]>(),
  date = JAN_15,
  categories = CATEGORIES,
}: AllocationFixture = {}) {
  return computeAllocation(daily, tickerIdx, buildDateIndex(daily), date, categories);
}

const computeJanCashflow = (txns: QianjiTxn[]) => computeCashflow(txns, JAN_START, JAN_END);
const computeJanActivity = (txns: InvestmentTxn[]) => computeActivity(txns, JAN_START, JAN_END);
const computeJanGroupedActivity = (txns: InvestmentTxn[]) => computeGroupedActivity(txns, JAN_START, JAN_END);
const computeJanCrossCheck = (investmentTxns: InvestmentTxn[], qianjiTxns: QianjiTxn[]) =>
  computeCrossCheck(investmentTxns, qianjiTxns, JAN_START, JAN_END);

function expectCashflowScalars(cf: ReturnType<typeof computeCashflow>, expected: Partial<ReturnType<typeof computeCashflow>>) {
  expect(cf).toMatchObject(expected);
}

const mkDeposit = (overrides: Partial<InvestmentTxn> = {}): InvestmentTxn =>
  mkInvestmentTxn({ actionType: "deposit", ticker: "", ...overrides });
const mkTransfer = (overrides: Partial<QianjiTxn> = {}): QianjiTxn =>
  mkQianjiTxn({ type: "transfer", ...overrides });
const mkQianjiFloor = (overrides: Partial<QianjiTxn> = {}): QianjiTxn =>
  mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 1, ...overrides });

type CrossCheckExpected = Partial<Pick<ReturnType<typeof computeCrossCheck>, "ok" | "matchedCount" | "totalCount">> & {
  allUnmatched?: number;
};

function expectCrossCheckTotals(cc: ReturnType<typeof computeCrossCheck>, { allUnmatched, ...expected }: CrossCheckExpected) {
  expect(cc).toMatchObject(expected);
  if (allUnmatched !== undefined) expect(cc.allUnmatched).toHaveLength(allUnmatched);
}

// ── buildDateIndex ──────────────────────────────────────────────────────

describe("buildDateIndex", () => {
  it("maps date strings to array indices", () => {
    const daily = mkDailyN(3);
    const idx = buildDateIndex(daily);
    expect(idx.get("2026-01-01")).toBe(0);
    expect(idx.get("2026-01-02")).toBe(1);
    expect(idx.get("2026-01-03")).toBe(2);
    expect(idx.get("2026-01-04")).toBeUndefined();
  });
});

// ── buildTickerIndex ────────────────────────────────────────────────────

describe("buildTickerIndex", () => {
  it("groups tickers by date", () => {
    const tickers: DailyTicker[] = [
      { date: "2026-01-01", ticker: "VTI", value: 1000, category: "US Equity", subtype: "broad" },
      { date: "2026-01-01", ticker: "VXUS", value: 500, category: "Non-US Equity", subtype: "broad" },
      { date: "2026-01-02", ticker: "VTI", value: 1010, category: "US Equity", subtype: "broad" },
    ];
    const idx = buildTickerIndex(tickers);
    expect(idx.get("2026-01-01")).toHaveLength(2);
    expect(idx.get("2026-01-02")).toHaveLength(1);
    expect(idx.get("2026-01-03")).toBeUndefined();
  });
});

it.each([
  ["date", () => buildDateIndex([])],
  ["ticker", () => buildTickerIndex([])],
])("returns empty %s map for empty array", (_name, buildIndex) => {
  expect(buildIndex().size).toBe(0);
});

// ── computeAllocation ───────────────────────────────────────────────────

describe("computeAllocation", () => {
  it("computes category percentages", () => {
    const result = computeDefaultAllocation();
    expect(result).not.toBeNull();
    expect(result!.total).toBe(100000);
    expect(result!.netWorth).toBe(95000); // 100000 + (-5000)
    expect(result!.categories).toHaveLength(4);
    const usEquity = result!.categories.find(c => c.name === "US Equity")!;
    expect(usEquity.pct).toBe(55);
  });

  it("returns null for unknown date", () => {
    expect(computeDefaultAllocation({ date: "2099-01-01" })).toBeNull();
  });

  it("handles zero total without NaN", () => {
    const daily = [mkDaily({ total: 0, usEquity: 0, nonUsEquity: 0, crypto: 0, safeNet: 0 })];
    const result = computeDefaultAllocation({ daily });
    expect(result).not.toBeNull();
    for (const cat of result!.categories) {
      expect(cat.pct).toBe(0);
    }
  });

  it("includes tickers from ticker index", () => {
    const tickerIdx = new Map<string, ApiTicker[]>([
      [JAN_15, [{ ticker: "VTI", value: 55000, category: "US Equity", subtype: "broad" }]],
    ]);
    const result = computeDefaultAllocation({ tickerIdx });
    expect(result!.tickers).toHaveLength(1);
    expect(result!.tickers[0].ticker).toBe("VTI");
  });

  it("honors custom target weights from bundle", () => {
    const customCats: CategoryMeta[] = CATEGORIES.map((cat, i) => ({ ...cat, targetPct: [60, 10, 5, 25][i] }));
    const result = computeDefaultAllocation({ categories: customCats })!;
    expect(result.categories.find(c => c.name === "US Equity")!.target).toBe(60);
  });
});

// ── catColorByName ──────────────────────────────────────────────────────

describe("catColorByName", () => {
  it("maps display names to Okabe-Ito colors via key", () => {
    const map = catColorByName(CATEGORIES);
    expect(map["US Equity"]).toBe(CAT_COLOR_BY_KEY.usEquity);
    expect(map["Crypto"]).toBe(CAT_COLOR_BY_KEY.crypto);
  });

  it("falls back to neutral grey for unknown keys", () => {
    const map = catColorByName([{ key: "unknown", name: "Alt", targetPct: 0 }]);
    expect(map["Alt"]).toBe("#888888");
  });
});

// ── computeCashflow ─────────────────────────────────────────────────────

describe("computeCashflow", () => {
  it("aggregates income and expenses", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "income", category: "Salary", amount: 5000 }),
      mkQianjiTxn({ type: "income", category: "Salary", amount: 5000, date: "2026-01-20" }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 2000 }),
      mkQianjiTxn({ type: "expense", category: "Food", amount: 500 }),
    ];
    const cf = computeJanCashflow(txns);
    expectCashflowScalars(cf, { totalIncome: 10000, totalExpenses: 2500, netCashflow: 7500 });
    expect(cf.incomeItems).toHaveLength(1);
    expect(cf.incomeItems[0].count).toBe(2);
    expect(cf.expenseItems).toHaveLength(2);
  });

  it.each([
    {
      name: "computes savings rate",
      txns: [
        mkQianjiTxn({ type: "income", amount: 10000 }),
        mkQianjiTxn({ type: "expense", category: "Rent", amount: 7000 }),
      ],
      expected: { savingsRate: 30 },
    },
    {
      name: "returns 0 savings rate when no income",
      txns: [mkQianjiTxn({ type: "expense", category: "Rent", amount: 1000 })],
      expected: { savingsRate: 0, takehomeSavingsRate: 0 },
    },
    {
      name: "deducts retirement-flagged income from take-home",
      txns: [
        mkQianjiTxn({ type: "income", category: "Salary", amount: 8000 }),
        mkQianjiTxn({ type: "income", category: "Employer Retirement Match", amount: 2000, isRetirement: true }),
        mkQianjiTxn({ type: "expense", category: "Rent", amount: 3000 }),
      ],
      expected: { totalIncome: 10000, takehomeSavingsRate: 62.5 },
    },
    {
      name: "ignores retirement flag on expense rows",
      txns: [
        mkQianjiTxn({ type: "income", category: "Salary", amount: 10000 }),
        mkQianjiTxn({ type: "expense", category: "Random", amount: 3000, isRetirement: true }),
      ],
      expected: { takehomeSavingsRate: 70 },
    },
    {
      name: "does not match 401 via substring — only the flag counts",
      txns: [
        mkQianjiTxn({ type: "income", category: "401K", amount: 2000 }),
        mkQianjiTxn({ type: "income", category: "Salary", amount: 8000 }),
        mkQianjiTxn({ type: "expense", category: "Rent", amount: 3000 }),
      ],
      expected: { takehomeSavingsRate: 70 },
    },
  ])("$name", ({ txns, expected }) => {
    expectCashflowScalars(computeJanCashflow(txns), expected);
  });

  it.each([
    {
      name: "counts CC repayments",
      txns: [
        mkQianjiTxn({ type: "repayment", category: "CC", amount: 1500 }),
        mkQianjiTxn({ type: "repayment", category: "CC", amount: 500 }),
      ],
      expected: { ccPayments: 2000 },
    },
    {
      name: "filters by date range",
      txns: [
        mkQianjiTxn({ date: "2025-12-31", type: "income", amount: 999 }),
        mkQianjiTxn({ date: JAN_15, type: "income", amount: 5000 }),
        mkQianjiTxn({ date: "2026-02-01", type: "income", amount: 999 }),
      ],
      expected: { totalIncome: 5000 },
    },
  ])("$name", ({ txns, expected }) => {
    expectCashflowScalars(computeJanCashflow(txns), expected);
  });

  it("sorts expense items by amount descending", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "expense", category: "Food", amount: 100 }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 2000 }),
      mkQianjiTxn({ type: "expense", category: "Gas", amount: 500 }),
    ];
    const cf = computeJanCashflow(txns);
    expect(cf.expenseItems.map(e => e.category)).toEqual(["Rent", "Gas", "Food"]);
  });
});

// ── normalizeInvestmentTxns ──────────────────────────────────────────────

describe("normalizeInvestmentTxns", () => {
  it("maps Fidelity txns 1:1 preserving actionType", () => {
    const f = [
      mkFidelityTxn({ runDate: "2026-01-10", actionType: "buy",  symbol: "VTI", amount: -500 }),
      mkFidelityTxn({ runDate: "2026-01-11", actionType: "sell", symbol: "GS",  amount:  600 }),
    ];
    const out = normalizeInvestmentTxns(f, [], []);
    expect(out).toEqual([
      { source: "fidelity", date: "2026-01-10", ticker: "VTI", actionType: "buy",  amount: -500, quantity: 2, price: 250 },
      { source: "fidelity", date: "2026-01-11", ticker: "GS",  actionType: "sell", amount:  600, quantity: 2, price: 250 },
    ]);
  });

  it("filters Robinhood actionKind='other' and keeps the rest", () => {
    const r = [
      mkRobinhoodTxn({ actionKind: "buy",     ticker: "AAPL", amountUsd: -200 }),
      mkRobinhoodTxn({ actionKind: "other",   ticker: "",     amountUsd: -1.5 }),
      mkRobinhoodTxn({ actionKind: "deposit", ticker: "",     amountUsd:  500 }),
    ];
    const out = normalizeInvestmentTxns([], r, []);
    expect(out).toHaveLength(2);
    expect(out.every((t) => t.source === "robinhood")).toBe(true);
    expect(out.map((t) => t.actionType)).toEqual(["buy", "deposit"]);
    expect(out[0]).toMatchObject({ quantity: 1, price: 200 });
  });

  it("maps all Empower contributions to actionType='contribution'", () => {
    const e = [
      mkEmpowerContribution({ date: "2026-01-15", amount: 450, ticker: "401k sp500" }),
      mkEmpowerContribution({ date: "2026-01-15", amount: 90,  ticker: "401k tech"  }),
    ];
    const out = normalizeInvestmentTxns([], [], e);
    expect(out).toEqual([
      { source: "401k", date: "2026-01-15", ticker: "401k sp500", actionType: "contribution", amount: 450, quantity: 0, price: 0 },
      { source: "401k", date: "2026-01-15", ticker: "401k tech",  actionType: "contribution", amount: 90,  quantity: 0, price: 0 },
    ]);
  });
});

// ── computeActivity ─────────────────────────────────────────────────────

describe("computeActivity", () => {
  it("aggregates buys, sells, dividends by ticker", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",      ticker: "VTI",  amount: -1000 }),
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",      ticker: "VTI",  amount: -500  }),
      mkInvestmentTxn({ source: "fidelity", actionType: "sell",     ticker: "AAPL", amount: 2000  }),
      mkInvestmentTxn({ source: "fidelity", actionType: "dividend", ticker: "VTI",  amount: 50    }),
    ];
    const act = computeJanActivity(txns);
    expect(act.buysBySymbol).toHaveLength(1);
    expect(act.buysBySymbol[0]).toEqual({ ticker: "VTI", count: 2, total: 1500, isGroup: false, sources: ["fidelity"] });
    expect(act.sellsBySymbol).toHaveLength(1);
    expect(act.sellsBySymbol[0].total).toBe(2000);
    expect(act.dividendsBySymbol).toHaveLength(1);
    expect(act.dividendsBySymbol[0].total).toBe(50);
  });

  it.each([
    {
      name: "counts reinvestment in both buys and dividends",
      txns: [mkInvestmentTxn({ source: "fidelity", actionType: "reinvestment", ticker: "VTI", amount: -100 })],
      assert: (act: ReturnType<typeof computeActivity>) => {
        expect(act.buysBySymbol).toHaveLength(1);
        expect(act.buysBySymbol[0].total).toBe(100);
        expect(act.dividendsBySymbol).toHaveLength(1);
        expect(act.dividendsBySymbol[0].total).toBe(100);
      },
    },
    {
      name: "skips transactions without ticker",
      txns: [mkInvestmentTxn({ source: "fidelity", actionType: "buy", ticker: "", amount: -500 })],
      assert: (act: ReturnType<typeof computeActivity>) => {
        expect(act.buysBySymbol).toHaveLength(0);
      },
    },
    {
      name: "filters by date range",
      txns: [
        mkInvestmentTxn({ source: "fidelity", date: "2025-12-31", actionType: "buy", ticker: "VTI", amount: -999 }),
        mkInvestmentTxn({ source: "fidelity", date: JAN_15, actionType: "buy", ticker: "VTI", amount: -500 }),
        mkInvestmentTxn({ source: "fidelity", date: "2026-02-01", actionType: "buy", ticker: "VTI", amount: -999 }),
      ],
      assert: (act: ReturnType<typeof computeActivity>) => {
        expect(act.buysBySymbol[0].total).toBe(500);
      },
    },
  ])("$name", ({ txns, assert }) => {
    assert(computeJanActivity(txns));
  });

  it("returns empty lists for no transactions", () => {
    const act = computeJanActivity([]);
    expect(act.buysBySymbol).toEqual([]);
    expect(act.sellsBySymbol).toEqual([]);
    expect(act.dividendsBySymbol).toEqual([]);
  });

  it("tracks sources per row across fidelity + 401k + robinhood", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "VOO",        amount: -500 }),
      mkInvestmentTxn({ source: "401k",      actionType: "contribution", ticker: "401k sp500", amount:  450 }),
      mkInvestmentTxn({ source: "robinhood", actionType: "buy",          ticker: "AAPL",       amount: -200 }),
    ];
    const a = computeJanActivity(txns);
    const voo = a.buysBySymbol.find((r) => r.ticker === "VOO")!;
    expect(voo.sources).toEqual(["fidelity"]);
    const k401 = a.buysBySymbol.find((r) => r.ticker === "401k sp500")!;
    expect(k401.sources).toEqual(["401k"]);
    const aapl = a.buysBySymbol.find((r) => r.ticker === "AAPL")!;
    expect(aapl.sources).toEqual(["robinhood"]);
  });
});

// ── computeCrossCheck ───────────────────────────────────────────────────

describe("computeCrossCheck", () => {
  it.each([
    {
      name: "matches deposit to transfer within 7-day window",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 })],
      qTxns: [mkTransfer({ date: "2026-01-16", amount: 1000 })],
      expected: { ok: true, matchedCount: 1, totalCount: 1 },
      assert: (cc: ReturnType<typeof computeCrossCheck>) => {
        expect(cc.perSource.fidelity.matched).toBe(1);
      },
    },
    {
      name: "fails match when transfer is outside 7-day window",
      txns: [mkDeposit({ source: "fidelity", date: JAN_START, amount: 1000 })],
      qTxns: [
        mkQianjiFloor({ amount: 50 }),
        mkTransfer({ date: JAN_15, amount: 1000 }),
      ],
      expected: { ok: false, matchedCount: 0, allUnmatched: 1 },
    },
    {
      name: "fails match when amounts differ",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 })],
      qTxns: [mkTransfer({ date: JAN_15, amount: 999.99 })],
      expected: { ok: false, matchedCount: 0 },
    },
  ])("$name", ({ txns, qTxns, expected, assert }) => {
    const cc = computeJanCrossCheck(txns, qTxns);
    expectCrossCheckTotals(cc, expected);
    assert?.(cc);
  });

  it("returns ok=false when no deposits exist", () => {
    const cc = computeJanCrossCheck([], []);
    expectCrossCheckTotals(cc, { ok: false, totalCount: 0 });
  });

  it("does not reuse a transfer for two deposits", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2026-01-10", amount: 500 }),
      mkDeposit({ source: "fidelity", date: "2026-01-11", amount: 500 }),
    ];
    const cc = computeJanCrossCheck(txns, [mkTransfer({ date: "2026-01-10", amount: 500 })]);
    expectCrossCheckTotals(cc, { matchedCount: 1, totalCount: 2, ok: false });
  });

  it("excludes sub-dollar dust deposits (cash sweep / residual interest)", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: JAN_15, amount: 0.03 }),
      mkDeposit({ source: "fidelity", date: JAN_15, amount: 0.33 }),
      mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiFloor({ amount: 50 }),
      mkTransfer({ date: JAN_15, amount: 1000 }),
    ];
    expectCrossCheckTotals(computeJanCrossCheck(txns, qTxns), { totalCount: 1, matchedCount: 1, ok: true });
  });

  it("excludes deposits predating the earliest Qianji txn", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2023-06-01", amount: 2000 }),
      mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2024-05-12", type: "expense", amount: 50 }), // establishes floor
      mkTransfer({ date: "2026-01-16", amount: 1000 }),
    ];
    const cc = computeCrossCheck(txns, qTxns, "2023-01-01", JAN_END);
    expectCrossCheckTotals(cc, { totalCount: 1, matchedCount: 1, ok: true });
  });

  it.each([
    {
      name: "matches deposit to income record booked directly to Fidelity",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 3346.27 })],
      qTxns: [mkQianjiTxn({ date: JAN_15, type: "income", category: "Salary", amount: 3346.27, accountTo: "Fidelity taxable" })],
      expected: { ok: true, matchedCount: 1 },
    },
    {
      name: "accountTo prefix match is case-insensitive",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 500 })],
      qTxns: [mkQianjiTxn({ date: JAN_15, type: "income", category: "Rewards", amount: 500, accountTo: "fidelity Roth IRA" })],
      expected: { matchedCount: 1 },
    },
    {
      name: "does not match income where accountTo is not Fidelity",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 })],
      qTxns: [
        mkQianjiFloor({ amount: 50 }),
        mkQianjiTxn({ date: JAN_15, type: "income", category: "Salary", amount: 1000, accountTo: "Chase Debit" }),
      ],
      expected: { matchedCount: 0 },
      assert: (cc: ReturnType<typeof computeCrossCheck>) => {
        expect(cc.perSource.fidelity.unmatched).toHaveLength(1);
      },
    },
    {
      name: "does not match income to Fidelity when amounts differ",
      txns: [mkDeposit({ source: "fidelity", date: JAN_15, amount: 1000 })],
      qTxns: [mkQianjiTxn({ date: JAN_15, type: "income", category: "Salary", amount: 999.99, accountTo: "Fidelity taxable" })],
      expected: { matchedCount: 0 },
    },
  ])("$name", ({ txns, qTxns, expected, assert }) => {
    const cc = computeJanCrossCheck(txns, qTxns);
    expectCrossCheckTotals(cc, expected);
    assert?.(cc);
  });

  // Earliest-in-window matching is optimal on interval bipartite graphs.
  // Nearest-available greedy would let the middle deposit steal the only
  // candidate the first deposit could reach, orphaning it — verify all three
  // now pair up cleanly.
  it("finds optimal matching where nearest-greedy would orphan", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2026-01-05", amount: 500 }),
      mkDeposit({ source: "fidelity", date: "2026-01-10", amount: 500 }),
      mkDeposit({ source: "fidelity", date: "2026-01-11", amount: 500 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkTransfer({ date: "2026-01-03", amount: 500 }),
      mkTransfer({ date: "2026-01-06", amount: 500 }),
      mkTransfer({ date: "2026-01-09", amount: 500 }),
    ];
    const cc = computeJanCrossCheck(txns, qTxns);
    expectCrossCheckTotals(cc, { matchedCount: 3, totalCount: 3, ok: true });
  });
});

// ── computeCrossCheck per-source ────────────────────────────────────────

describe("computeCrossCheck per-source", () => {
  it("invariant: matchedCount === perSource.fidelity.matched + perSource.robinhood.matched", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2026-01-10", amount: 500 }),
      mkDeposit({ source: "robinhood", date: "2026-01-12", amount: 200 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiFloor(),
      mkTransfer({ date: "2026-01-10", amount: 500, accountTo: "Fidelity taxable" }),
      mkTransfer({ date: "2026-01-12", amount: 200, accountTo: "Robinhood" }),
    ];
    const cc = computeJanCrossCheck(txns, q);
    expect(cc.matchedCount).toBe(cc.perSource.fidelity.matched + cc.perSource.robinhood.matched);
    expect(cc.totalCount).toBe(cc.perSource.fidelity.total + cc.perSource.robinhood.total);
    expect(cc.ok).toBe(true);
  });

  it("ignores 401k contributions entirely (not part of UI cross-check)", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2026-01-10", amount: 500 }),
      // 401k contributions present but should NOT affect cross-check — pipeline handles QFX/Qianji reconcile at ingest
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k sp500", date: "2026-01-15", amount: 450 }),
      mkInvestmentTxn({ source: "401k", actionType: "contribution", ticker: "401k tech",  date: "2026-01-15", amount: 90 }),
    ];
    const q: QianjiTxn[] = [
      mkQianjiFloor(),
      mkTransfer({ date: "2026-01-10", amount: 500, accountTo: "Fidelity taxable" }),
    ];
    const cc = computeJanCrossCheck(txns, q);
    expectCrossCheckTotals(cc, { totalCount: 1, matchedCount: 1 }); // only the Fidelity deposit
    expect(cc.perSource).not.toHaveProperty("contribution");
    expect("401k" in cc.perSource).toBe(false);
  });

  it("Robinhood deposit matches only against Qianji with accountTo starting 'robinhood'", () => {
    const txns: InvestmentTxn[] = [mkDeposit({ source: "robinhood", date: JAN_15, amount: 500 })];
    const q: QianjiTxn[] = [
      mkQianjiFloor(),
      mkQianjiTxn({ date: JAN_15, type: "income", amount: 500, accountTo: "Fidelity taxable" }), // wrong account
    ];
    const cc = computeJanCrossCheck(txns, q);
    expect(cc.perSource.robinhood.matched).toBe(0);
    expect(cc.perSource.robinhood.unmatched).toHaveLength(1);
    expect(cc.allUnmatched).toHaveLength(1);
    expect(cc.allUnmatched[0].source).toBe("robinhood");
  });

  it("surfaces unmatched items on allUnmatched (flat list for drawer)", () => {
    const txns: InvestmentTxn[] = [
      mkDeposit({ source: "fidelity", date: "2026-01-10", amount: 999 }),
      mkDeposit({ source: "robinhood", date: "2026-01-12", amount: 200 }),
    ];
    const cc = computeJanCrossCheck(txns, [mkQianjiFloor()]);
    expectCrossCheckTotals(cc, { matchedCount: 0, totalCount: 2, allUnmatched: 2 });
    expect(cc.allUnmatched.map(u => u.source).sort()).toEqual(["fidelity", "robinhood"]);
  });
});

// ── computeGroupedActivity ──────────────────────────────────────────────

describe("computeGroupedActivity", () => {
  it("folds group tickers into one row per side", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "sell", ticker: "SPY",  amount: -1000 }),
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",  ticker: "VOO",  amount:  500 }),
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",  ticker: "NVDA", amount: 2000 }),
    ];
    const act = computeJanGroupedActivity(txns);
    expect(act.sellsBySymbol).toContainEqual(expect.objectContaining({ ticker: "S&P 500", count: 1, total: 1000, isGroup: true, groupKey: "sp500" }));
    expect(act.buysBySymbol).toContainEqual(expect.objectContaining({ ticker: "S&P 500", count: 1, total: 500, isGroup: true, groupKey: "sp500" }));
    expect(act.buysBySymbol).toContainEqual(expect.objectContaining({ ticker: "NVDA", count: 1, total: 2000, isGroup: false }));
    expect(act.sellsBySymbol.find(r => r.ticker === "SPY")).toBeUndefined();
    expect(act.buysBySymbol.find(r => r.ticker === "VOO")).toBeUndefined();
  });

  it("dividends remain per-ticker (not grouped)", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "dividend", ticker: "VOO", amount: 10 }),
      mkInvestmentTxn({ source: "fidelity", actionType: "dividend", ticker: "SPY", amount: 5 }),
    ];
    const act = computeJanGroupedActivity(txns);
    expect(act.dividendsBySymbol.map(r => r.ticker).sort()).toEqual(["SPY", "VOO"]);
  });

  it("aggregates sources across group members (VOO + FXAIX + 401k sp500 → S&P 500)", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "VOO",          amount: -500 }),
      mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "FXAIX",        amount: -100 }),
      mkInvestmentTxn({ source: "401k",      actionType: "contribution", ticker: "401k sp500",   amount:  450 }),
    ];
    const g = computeJanGroupedActivity(txns);
    const spRow = g.buysBySymbol.find((r) => r.ticker === "S&P 500")!;
    expect(spRow.isGroup).toBe(true);
    expect([...spRow.sources!].sort()).toEqual(["401k", "fidelity"]);
    expect(spRow.total).toBe(1050);
  });
});

// ── computeMonthlyFlows ─────────────────────────────────────────────────

describe("computeMonthlyFlows", () => {
  it("aggregates by month", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-05", type: "income", amount: 5000 }),
      mkQianjiTxn({ date: "2026-01-20", type: "expense", category: "Rent", amount: 2000 }),
      mkQianjiTxn({ date: "2026-02-05", type: "income", amount: 5000 }),
      mkQianjiTxn({ date: "2026-02-20", type: "expense", category: "Rent", amount: 1500 }),
    ];
    const flows = computeMonthlyFlows(txns, JAN_START, "2026-02-28");
    expect(flows).toHaveLength(2);
    expect(flows[0].month).toBe("2026-01");
    expect(flows[0].income).toBe(5000);
    expect(flows[0].expenses).toBe(2000);
    expect(flows[0].savingsRate).toBe(60); // (5000-2000)/5000*100
    expect(flows[1].month).toBe("2026-02");
  });

  it("returns empty for null start/end", () => {
    expect(computeMonthlyFlows([], null, null)).toEqual([]);
    expect(computeMonthlyFlows([mkQianjiTxn()], null, "2026-12-31")).toEqual([]);
  });

  it("sorts months chronologically", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-03-01", type: "income", amount: 100 }),
      mkQianjiTxn({ date: "2026-01-01", type: "income", amount: 100 }),
      mkQianjiTxn({ date: "2026-02-01", type: "income", amount: 100 }),
    ];
    const flows = computeMonthlyFlows(txns, JAN_START, "2026-03-31");
    expect(flows.map(f => f.month)).toEqual(["2026-01", "2026-02", "2026-03"]);
  });

  it.each([
    { name: "returns empty for no transactions", txns: [], start: JAN_START, end: "2026-12-31" },
    {
      name: "drops months with no income (cannot compute savings rate)",
      txns: [mkQianjiTxn({ date: JAN_15, type: "expense", category: "Food", amount: 100 })],
      start: JAN_START,
      end: JAN_END,
    },
    {
      name: "ignores non-income/expense types",
      txns: [
        mkQianjiTxn({ date: JAN_15, type: "transfer", amount: 5000 }),
        mkQianjiTxn({ date: JAN_15, type: "repayment", amount: 1000 }),
      ],
      start: JAN_START,
      end: JAN_END,
    },
  ])("$name", ({ txns, start, end }) => {
    expect(computeMonthlyFlows(txns, start, end)).toEqual([]);
  });
});
