import { describe, it, expect } from "vitest";
import type { CategoryMeta, DailyTicker, FidelityTxn, QianjiTxn } from "@/lib/schemas";
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
  normalizeInvestmentTxns,
  type InvestmentTxn,
} from "@/lib/compute/compute";
import { CAT_COLOR_BY_KEY } from "@/lib/format/chart-colors";
import { CATEGORIES, mkDaily, mkDailyN, mkFidelityTxn, mkQianjiTxn, mkRobinhoodTxn, mkEmpowerContribution, mkInvestmentTxn } from "@/test/factories";

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

// ── normalizeInvestmentTxns ──────────────────────────────────────────────

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

// ── computeActivity ─────────────────────────────────────────────────────

describe("computeActivity", () => {
  it("aggregates buys, sells, dividends by ticker", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",      ticker: "VTI",  amount: -1000 }),
      mkInvestmentTxn({ source: "fidelity", actionType: "buy",      ticker: "VTI",  amount: -500  }),
      mkInvestmentTxn({ source: "fidelity", actionType: "sell",     ticker: "AAPL", amount: 2000  }),
      mkInvestmentTxn({ source: "fidelity", actionType: "dividend", ticker: "VTI",  amount: 50    }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(1);
    expect(act.buysBySymbol[0]).toEqual({ ticker: "VTI", count: 2, total: 1500, isGroup: false, sources: ["fidelity"] });
    expect(act.sellsBySymbol).toHaveLength(1);
    expect(act.sellsBySymbol[0].total).toBe(2000);
    expect(act.dividendsBySymbol).toHaveLength(1);
    expect(act.dividendsBySymbol[0].total).toBe(50);
  });

  it("counts reinvestment in both buys and dividends", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "reinvestment", ticker: "VTI", amount: -100 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(1);
    expect(act.buysBySymbol[0].total).toBe(100);
    expect(act.dividendsBySymbol).toHaveLength(1);
    expect(act.dividendsBySymbol[0].total).toBe(100);
  });

  it("skips transactions without ticker", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", actionType: "buy", ticker: "", amount: -500 }),
    ];
    const act = computeActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.buysBySymbol).toHaveLength(0);
  });

  it("filters by date range", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity", date: "2025-12-31", actionType: "buy", ticker: "VTI", amount: -999 }),
      mkInvestmentTxn({ source: "fidelity", date: "2026-01-15", actionType: "buy", ticker: "VTI", amount: -500 }),
      mkInvestmentTxn({ source: "fidelity", date: "2026-02-01", actionType: "buy", ticker: "VTI", amount: -999 }),
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

  it("tracks sources per row across fidelity + 401k + robinhood", () => {
    const txns: InvestmentTxn[] = [
      mkInvestmentTxn({ source: "fidelity",  actionType: "buy",          ticker: "VOO",        amount: -500 }),
      mkInvestmentTxn({ source: "401k",      actionType: "contribution", ticker: "401k sp500", amount:  450 }),
      mkInvestmentTxn({ source: "robinhood", actionType: "buy",          ticker: "AAPL",       amount: -200 }),
    ];
    const a = computeActivity(txns, "2026-01-01", "2026-01-31");
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
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 50 }), // anchor Qianji floor before deposit
      mkQianjiTxn({ date: "2026-01-15", type: "transfer", amount: 1000 }), // 14 days after deposit
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

  it("excludes sub-dollar dust deposits (cash sweep / residual interest)", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 0.03 }),
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 0.33 }),
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 50 }), // anchor Qianji floor
      mkQianjiTxn({ date: "2026-01-15", type: "transfer", amount: 1000 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.totalCount).toBe(1);
    expect(cc.matchedCount).toBe(1);
    expect(cc.ok).toBe(true);
  });

  it("excludes deposits predating the earliest Qianji txn", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2023-06-01", actionType: "deposit", symbol: "", amount: 2000 }), // pre-Qianji
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2024-05-12", type: "expense", amount: 50 }), // establishes floor
      mkQianjiTxn({ date: "2026-01-16", type: "transfer", amount: 1000 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2023-01-01", "2026-01-31");
    expect(cc.totalCount).toBe(1);
    expect(cc.matchedCount).toBe(1);
    expect(cc.ok).toBe(true);
  });

  // Direct-to-Fidelity income (payroll direct deposit, rebate rewards) is
  // logged as type=income with accountTo="Fidelity …" — not a transfer.
  it("matches deposit to income record booked directly to Fidelity", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 3346.27 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "income", category: "Salary", amount: 3346.27, accountTo: "Fidelity taxable" }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.ok).toBe(true);
    expect(cc.matchedCount).toBe(1);
  });

  it("accountTo prefix match is case-insensitive", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 500 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "income", category: "Rewards", amount: 500, accountTo: "fidelity Roth IRA" }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(1);
  });

  it("does not match income where accountTo is not Fidelity", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2025-12-01", type: "expense", amount: 50 }), // anchor Qianji floor
      mkQianjiTxn({ date: "2026-01-15", type: "income", category: "Salary", amount: 1000, accountTo: "Chase Debit" }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(0);
    expect(cc.unmatchedTotal).toBe(1000);
  });

  it("does not match income to Fidelity when amounts differ", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-15", actionType: "deposit", symbol: "", amount: 1000 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-15", type: "income", category: "Salary", amount: 999.99, accountTo: "Fidelity taxable" }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(0);
  });

  // Earliest-in-window matching is optimal on interval bipartite graphs.
  // Nearest-available greedy would let the middle deposit steal the only
  // candidate the first deposit could reach, orphaning it — verify all three
  // now pair up cleanly.
  it("finds optimal matching where nearest-greedy would orphan", () => {
    const fTxns: FidelityTxn[] = [
      mkFidelityTxn({ runDate: "2026-01-05", actionType: "deposit", symbol: "", amount: 500 }),
      mkFidelityTxn({ runDate: "2026-01-10", actionType: "deposit", symbol: "", amount: 500 }),
      mkFidelityTxn({ runDate: "2026-01-11", actionType: "deposit", symbol: "", amount: 500 }),
    ];
    const qTxns: QianjiTxn[] = [
      mkQianjiTxn({ date: "2026-01-03", type: "transfer", amount: 500 }),
      mkQianjiTxn({ date: "2026-01-06", type: "transfer", amount: 500 }),
      mkQianjiTxn({ date: "2026-01-09", type: "transfer", amount: 500 }),
    ];
    const cc = computeCrossCheck(fTxns, qTxns, "2026-01-01", "2026-01-31");
    expect(cc.matchedCount).toBe(3);
    expect(cc.totalCount).toBe(3);
    expect(cc.ok).toBe(true);
  });
});

// ── computeGroupedActivity ──────────────────────────────────────────────

import { computeGroupedActivity } from "./compute";

describe("computeGroupedActivity", () => {
  it("aggregates group tickers into one row (net-sell cluster)", () => {
    const txns: FidelityTxn[] = [
      { runDate: "2026-01-02", actionType: "sell", symbol: "SPY", amount: -1000, quantity: 5, price: 200 },
      { runDate: "2026-01-02", actionType: "buy",  symbol: "VOO", amount:  500, quantity: 1, price: 500 },
      { runDate: "2026-01-03", actionType: "buy",  symbol: "NVDA", amount: 2000, quantity: 10, price: 200 },
    ];
    const act = computeGroupedActivity(txns, "2026-01-01", "2026-01-31");
    expect(act.sellsBySymbol).toContainEqual(expect.objectContaining({ ticker: "S&P 500", count: 1, total: 500, isGroup: true, groupKey: "sp500" }));
    expect(act.buysBySymbol).toContainEqual(expect.objectContaining({ ticker: "NVDA", count: 1, total: 2000, isGroup: false }));
    expect(act.sellsBySymbol.find(r => r.ticker === "SPY")).toBeUndefined();
    expect(act.buysBySymbol.find(r => r.ticker === "VOO")).toBeUndefined();
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
    expect(act.dividendsBySymbol.map(r => r.ticker).sort()).toEqual(["SPY", "VOO"]);
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
