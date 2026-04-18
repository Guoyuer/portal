import { describe, it, expect } from "vitest";
import type { CategoryMeta, DailyPoint, DailyTicker, FidelityTxn, QianjiTxn } from "./schemas";
import {
  computeAllocation,
  computeCashflow,
  computeActivity,
  computeCrossCheck,
  computeMonthlyFlows,
  buildDateIndex,
  buildTickerIndex,
  catColorByName,
  cashflowState,
} from "./compute";
import { CAT_COLOR_BY_KEY } from "./chart-colors";

// Canonical category metadata (matches the pipeline default; each test is
// isolated — if a test needs a different shape it declares its own).
const CATEGORIES: CategoryMeta[] = [
  { key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 55 },
  { key: "nonUsEquity", name: "Non-US Equity", displayOrder: 1, targetPct: 15 },
  { key: "crypto", name: "Crypto", displayOrder: 2, targetPct: 3 },
  { key: "safeNet", name: "Safe Net", displayOrder: 3, targetPct: 27 },
];

// ── Helpers ─────────────────────────────────────────────────────────────

function mkDaily(overrides: Partial<DailyPoint> = {}): DailyPoint {
  return { date: "2026-01-15", total: 100000, usEquity: 55000, nonUsEquity: 15000, crypto: 3000, safeNet: 27000, liabilities: -5000, ...overrides };
}

function mkDailyN(n: number): DailyPoint[] {
  return Array.from({ length: n }, (_, i) => mkDaily({ date: `2026-01-${String(i + 1).padStart(2, "0")}` }));
}

function mkFidelityTxn(overrides: Partial<FidelityTxn> = {}): FidelityTxn {
  return { runDate: "2026-01-15", actionType: "buy", symbol: "VTI", amount: -500, quantity: 2, price: 250, ...overrides };
}

function mkQianjiTxn(overrides: Partial<QianjiTxn> = {}): QianjiTxn {
  return { date: "2026-01-15", type: "income", category: "Salary", amount: 5000, isRetirement: false, ...overrides };
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

  it("returns empty map for empty array", () => {
    expect(buildDateIndex([]).size).toBe(0);
  });
});

// ── buildTickerIndex ────────────────────────────────────────────────────

describe("buildTickerIndex", () => {
  it("groups tickers by date", () => {
    const tickers: DailyTicker[] = [
      { date: "2026-01-01", ticker: "VTI", value: 1000, category: "US Equity", subtype: "broad", costBasis: 900, gainLoss: 100, gainLossPct: 11.1 },
      { date: "2026-01-01", ticker: "VXUS", value: 500, category: "Non-US Equity", subtype: "broad", costBasis: 450, gainLoss: 50, gainLossPct: 11.1 },
      { date: "2026-01-02", ticker: "VTI", value: 1010, category: "US Equity", subtype: "broad", costBasis: 900, gainLoss: 110, gainLossPct: 12.2 },
    ];
    const idx = buildTickerIndex(tickers);
    expect(idx.get("2026-01-01")).toHaveLength(2);
    expect(idx.get("2026-01-02")).toHaveLength(1);
    expect(idx.get("2026-01-03")).toBeUndefined();
  });

  it("returns empty map for empty array", () => {
    expect(buildTickerIndex([]).size).toBe(0);
  });
});

// ── computeAllocation ───────────────────────────────────────────────────

describe("computeAllocation", () => {
  it("computes category percentages and deviation", () => {
    const daily = [mkDaily()];
    const dateIdx = buildDateIndex(daily);
    const tickerIdx = new Map();
    const result = computeAllocation(daily, tickerIdx, dateIdx, "2026-01-15", CATEGORIES);
    expect(result).not.toBeNull();
    expect(result!.total).toBe(100000);
    expect(result!.netWorth).toBe(95000); // 100000 + (-5000)
    expect(result!.categories).toHaveLength(4);
    const usEquity = result!.categories.find(c => c.name === "US Equity")!;
    expect(usEquity.pct).toBe(55);
    expect(usEquity.deviation).toBe(0); // 55% actual, 55% target
  });

  it("returns null for unknown date", () => {
    const daily = [mkDaily()];
    const dateIdx = buildDateIndex(daily);
    expect(computeAllocation(daily, new Map(), dateIdx, "2099-01-01", CATEGORIES)).toBeNull();
  });

  it("handles zero total without NaN", () => {
    const daily = [mkDaily({ total: 0, usEquity: 0, nonUsEquity: 0, crypto: 0, safeNet: 0 })];
    const dateIdx = buildDateIndex(daily);
    const result = computeAllocation(daily, new Map(), dateIdx, "2026-01-15", CATEGORIES);
    expect(result).not.toBeNull();
    for (const cat of result!.categories) {
      expect(cat.pct).toBe(0);
      expect(Number.isFinite(cat.deviation)).toBe(true);
    }
  });

  it("includes tickers from ticker index", () => {
    const daily = [mkDaily()];
    const dateIdx = buildDateIndex(daily);
    const tickerIdx = new Map([["2026-01-15", [{ ticker: "VTI", value: 55000, category: "US Equity", subtype: "broad", costBasis: 50000, gainLoss: 5000, gainLossPct: 10 }]]]);
    const result = computeAllocation(daily, tickerIdx, dateIdx, "2026-01-15", CATEGORIES);
    expect(result!.tickers).toHaveLength(1);
    expect(result!.tickers[0].ticker).toBe("VTI");
  });

  it("honors custom target weights from bundle", () => {
    const daily = [mkDaily()];
    const dateIdx = buildDateIndex(daily);
    const customCats: CategoryMeta[] = [
      { key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 60 },
      { key: "nonUsEquity", name: "Non-US Equity", displayOrder: 1, targetPct: 10 },
      { key: "crypto", name: "Crypto", displayOrder: 2, targetPct: 5 },
      { key: "safeNet", name: "Safe Net", displayOrder: 3, targetPct: 25 },
    ];
    const result = computeAllocation(daily, new Map(), dateIdx, "2026-01-15", customCats)!;
    expect(result.categories.find(c => c.name === "US Equity")!.target).toBe(60);
    // 55% actual vs 60% target → deviation -5
    expect(result.categories.find(c => c.name === "US Equity")!.deviation).toBe(-5);
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
    const map = catColorByName([{ key: "unknown", name: "Alt", displayOrder: 0, targetPct: 0 }]);
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
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.totalIncome).toBe(10000);
    expect(cf.totalExpenses).toBe(2500);
    expect(cf.netCashflow).toBe(7500);
    expect(cf.incomeItems).toHaveLength(1);
    expect(cf.incomeItems[0].count).toBe(2);
    expect(cf.expenseItems).toHaveLength(2);
  });

  it("computes savings rate", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "income", amount: 10000 }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 7000 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.savingsRate).toBe(30); // (10000-7000)/10000 * 100
  });

  it("returns 0 savings rate when no income", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 1000 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.savingsRate).toBe(0);
    expect(cf.takehomeSavingsRate).toBe(0);
  });

  it("deducts retirement-flagged income from take-home", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "income", category: "Salary", amount: 8000 }),
      mkQianjiTxn({ type: "income", category: "Employer Retirement Match", amount: 2000, isRetirement: true }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 3000 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.totalIncome).toBe(10000);
    // takehome = 10000 - 2000 = 8000; rate = (8000-3000)/8000 = 62.5%
    expect(cf.takehomeSavingsRate).toBe(62.5);
  });

  it("ignores retirement flag on expense rows", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "income", category: "Salary", amount: 10000 }),
      // isRetirement on an expense must not affect the income deduction
      mkQianjiTxn({ type: "expense", category: "Random", amount: 3000, isRetirement: true }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.takehomeSavingsRate).toBe(70); // (10000-3000)/10000
  });

  it("does not match 401 via substring — only the flag counts", () => {
    const txns: QianjiTxn[] = [
      // Historic behavior relied on category name containing '401'.
      // With the flag-based approach, an income tagged '401K' WITHOUT the
      // isRetirement flag should be counted as take-home income.
      mkQianjiTxn({ type: "income", category: "401K", amount: 2000 }),
      mkQianjiTxn({ type: "income", category: "Salary", amount: 8000 }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 3000 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    // No flag → includes '401K' in take-home: (10000-3000)/10000 = 70%
    expect(cf.takehomeSavingsRate).toBe(70);
  });

  it("counts CC repayments", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "repayment", category: "CC", amount: 1500 }),
      mkQianjiTxn({ type: "repayment", category: "CC", amount: 500 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.ccPayments).toBe(2000);
  });

  it("filters by date range", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-31", type: "income", amount: 999 }), // before
      mkQianjiTxn({ date: "2026-01-15", type: "income", amount: 5000 }), // in range
      mkQianjiTxn({ date: "2026-02-01", type: "income", amount: 999 }), // after
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.totalIncome).toBe(5000);
  });

  it("sorts expense items by amount descending", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ type: "expense", category: "Food", amount: 100 }),
      mkQianjiTxn({ type: "expense", category: "Rent", amount: 2000 }),
      mkQianjiTxn({ type: "expense", category: "Gas", amount: 500 }),
    ];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    expect(cf.expenseItems.map(e => e.category)).toEqual(["Rent", "Gas", "Food"]);
  });
});

// ── cashflowState ───────────────────────────────────────────────────────

describe("cashflowState", () => {
  it("returns unavailable when cashflow is null", () => {
    expect(cashflowState(null)).toEqual({ kind: "unavailable" });
  });

  it("returns empty when both totals are zero", () => {
    const cf = computeCashflow([], "2026-01-01", "2026-01-31");
    expect(cashflowState(cf)).toEqual({ kind: "empty" });
  });

  it("returns data with the original cashflow attached when there is activity", () => {
    const txns: QianjiTxn[] = [mkQianjiTxn({ type: "income", amount: 100 })];
    const cf = computeCashflow(txns, "2026-01-01", "2026-01-31");
    const state = cashflowState(cf);
    expect(state.kind).toBe("data");
    if (state.kind === "data") expect(state.data).toBe(cf);
  });
});

// ── computeActivity ─────────────────────────────────────────────────────

describe("computeActivity", () => {
  it("aggregates buys, sells, dividends by symbol", () => {
    const txns: FidelityTxn[] = [
      mkFidelityTxn({ actionType: "buy", symbol: "VTI", amount: -1000 }),
      mkFidelityTxn({ actionType: "buy", symbol: "VTI", amount: -500 }),
      mkFidelityTxn({ actionType: "sell", symbol: "AAPL", amount: 2000 }),
      mkFidelityTxn({ actionType: "dividend", symbol: "VTI", amount: 50 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(1);
    expect(act.buysBySymbol[0]).toEqual({ symbol: "VTI", count: 2, total: 1500 });
    expect(act.sellsBySymbol).toHaveLength(1);
    expect(act.sellsBySymbol[0].total).toBe(2000);
    expect(act.dividendsBySymbol).toHaveLength(1);
    expect(act.dividendsBySymbol[0].total).toBe(50);
  });

  it("counts reinvestment in both buys and dividends", () => {
    const txns: FidelityTxn[] = [
      mkFidelityTxn({ actionType: "reinvestment", symbol: "VTI", amount: -100 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(1);
    expect(act.buysBySymbol[0].total).toBe(100);
    expect(act.dividendsBySymbol).toHaveLength(1);
    expect(act.dividendsBySymbol[0].total).toBe(100);
  });

  it("skips transactions without symbol", () => {
    const txns: FidelityTxn[] = [
      mkFidelityTxn({ actionType: "buy", symbol: "", amount: -500 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(0);
  });

  it("filters by date range", () => {
    const txns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2025-12-31", actionType: "buy", symbol: "VTI", amount: -999 }),
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "buy", symbol: "VTI", amount: -500 }),
      mkFidelityTxn({ runDate: "2026-02-01", actionType: "buy", symbol: "VTI", amount: -999 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol[0].total).toBe(500);
  });

  it("returns empty lists for no transactions", () => {
    const act = computeActivity([], "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toEqual([]);
    expect(act.sellsBySymbol).toEqual([]);
    expect(act.dividendsBySymbol).toEqual([]);
  });
});

// ── computeCrossCheck ───────────────────────────────────────────────────

describe("computeCrossCheck", () => {
  it("matches deposit to transfer within 7-day window", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-16", type: "transfer", amount: 1000 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.ok).toBe(true);
    expect(cc.matchedCount).toBe(1);
    expect(cc.totalCount).toBe(1);
    expect(cc.fidelityTotal).toBe(1000);
    expect(cc.unmatchedTotal).toBe(0);
  });

  it("fails match when transfer is outside 7-day window", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-01", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "transfer", amount: 1000 }), // 14 days later
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.ok).toBe(false);
    expect(cc.matchedCount).toBe(0);
    expect(cc.unmatchedTotal).toBe(1000);
  });

  it("fails match when amounts differ", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "transfer", amount: 999.99 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.ok).toBe(false);
    expect(cc.matchedCount).toBe(0);
  });

  it("returns ok=false when no deposits exist", () => {
    const cc = computeCrossCheck([], [], "2026-01-01", "2026-01-31");
    expect(cc.ok).toBe(false);
    expect(cc.totalCount).toBe(0);
  });

  it("does not reuse a transfer for two deposits", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-10", actionType: "deposit", symbol: "", amount: 500 }),
      mkFidelityTxn({ runDate: "2026-01-11", actionType: "deposit", symbol: "", amount: 500 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-10", type: "transfer", amount: 500 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(1);
    expect(cc.totalCount).toBe(2);
    expect(cc.ok).toBe(false);
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
    const flows = computeMonthlyFlows(txns, "2026-01-01", "2026-02-28");
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

  it("returns empty for no transactions", () => {
    expect(computeMonthlyFlows([], "2026-01-01", "2026-12-31")).toEqual([]);
  });

  it("sorts months chronologically", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-03-01", type: "income", amount: 100 }),
      mkQianjiTxn({ date: "2026-01-01", type: "income", amount: 100 }),
      mkQianjiTxn({ date: "2026-02-01", type: "income", amount: 100 }),
    ];
    const flows = computeMonthlyFlows(txns, "2026-01-01", "2026-03-31");
    expect(flows.map(f => f.month)).toEqual(["2026-01", "2026-02", "2026-03"]);
  });

  it("returns 0 savings rate when income is 0", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "expense", category: "Food", amount: 100 }),
    ];
    const flows = computeMonthlyFlows(txns, "2026-01-01", "2026-01-31");
    expect(flows[0].savingsRate).toBe(0);
  });

  it("ignores non-income/expense types", () => {
    const txns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "transfer", amount: 5000 }),
      mkQianjiTxn({ date: "2026-01-15", type: "repayment", amount: 1000 }),
    ];
    const flows = computeMonthlyFlows(txns, "2026-01-01", "2026-01-31");
    expect(flows).toHaveLength(0); // no income/expense → no months recorded
  });
});
