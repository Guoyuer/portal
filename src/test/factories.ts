// Shared builders for vitest suites. Each factory accepts a partial override
// so tests only spell out the fields that matter to the assertion.
//
// Canonical category metadata matches the pipeline default (Okabe-Ito 55/15/3/27).
// Canonical date is 2026-01-15 — lines up with compute fixtures and mkDaily.

import type {
  CategoryMeta,
  DailyPoint,
  FidelityTxn,
  QianjiTxn,
  RobinhoodTxn,
  EmpowerContribution,
  MarketData,
} from "@/lib/schemas/timeline";
import type { InvestmentTxn } from "@/lib/compute/compute";
import type {
  ApiCategory,
  ApiTicker,
  CashflowResponse,
  ActivityResponse,
} from "@/lib/compute/computed-types";

export const CATEGORIES: CategoryMeta[] = [
  { key: "usEquity", name: "US Equity", targetPct: 55 },
  { key: "nonUsEquity", name: "Non-US Equity", targetPct: 15 },
  { key: "crypto", name: "Crypto", targetPct: 3 },
  { key: "safeNet", name: "Safe Net", targetPct: 27 },
];

export const COLOR_BY_NAME: Record<string, string> = {
  "US Equity": "#0072B2",
  "Non-US Equity": "#009E73",
  "Crypto": "#E69F00",
  "Safe Net": "#56B4E9",
};

export function mkDaily(overrides: Partial<DailyPoint> = {}): DailyPoint {
  return {
    date: "2026-01-15",
    total: 100000,
    usEquity: 55000,
    nonUsEquity: 15000,
    crypto: 3000,
    safeNet: 27000,
    liabilities: -5000,
    ...overrides,
  };
}

export function mkDailyN(n: number, start = "2026-01-01"): DailyPoint[] {
  const [y, m] = start.split("-").map(Number);
  return Array.from({ length: n }, (_, i) =>
    mkDaily({ date: `${y}-${String(m).padStart(2, "0")}-${String(i + 1).padStart(2, "0")}` }),
  );
}

export function mkFidelityTxn(overrides: Partial<FidelityTxn> = {}): FidelityTxn {
  return {
    runDate: "2026-01-15",
    actionType: "buy",
    symbol: "VTI",
    amount: -500,
    quantity: 2,
    price: 250,
    ...overrides,
  };
}

export function mkQianjiTxn(overrides: Partial<QianjiTxn> = {}): QianjiTxn {
  return {
    date: "2026-01-15",
    type: "income",
    category: "Salary",
    amount: 5000,
    isRetirement: false,
    accountTo: "",
    ...overrides,
  };
}

export function mkRobinhoodTxn(overrides: Partial<RobinhoodTxn> = {}): RobinhoodTxn {
  return {
    txnDate: "2026-01-15",
    actionKind: "buy",
    ticker: "AAPL",
    quantity: 1,
    amountUsd: -200,
    ...overrides,
  };
}

export function mkEmpowerContribution(overrides: Partial<EmpowerContribution> = {}): EmpowerContribution {
  return {
    date: "2026-01-15",
    amount: 450,
    ticker: "401k sp500",
    ...overrides,
  };
}

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

export function mkApiTicker(overrides: Partial<ApiTicker> = {}): ApiTicker {
  return {
    ticker: "X",
    value: 100,
    category: "US Equity",
    subtype: "Broad",
    ...overrides,
  };
}

export function mkApiCategory(name: string, value: number, overrides: Partial<ApiCategory> = {}): ApiCategory {
  return { name, value, pct: 0, target: 0, deviation: 0, ...overrides };
}

export const SNAPSHOT: DailyPoint = mkDaily();

export const CASHFLOW: CashflowResponse = {
  incomeItems: [{ category: "Salary", amount: 5000, count: 1 }],
  expenseItems: [{ category: "Rent", amount: 2000, count: 1 }],
  totalIncome: 5000,
  totalExpenses: 2000,
  netCashflow: 3000,
  ccPayments: 500,
  savingsRate: 60,
  takehomeSavingsRate: 55,
};

export const ACTIVITY: ActivityResponse = {
  buysBySymbol: [{ ticker: "VTI", count: 2, total: 1000, isGroup: false, sources: ["fidelity"] }],
  sellsBySymbol: [],
  dividendsBySymbol: [{ ticker: "SCHD", count: 1, total: 50, isGroup: false, sources: ["fidelity"] }],
};

export const MARKET: MarketData = {
  indices: [
    { ticker: "^GSPC", name: "S&P 500", monthReturn: 2.5, ytdReturn: 12.3, current: 5500, sparkline: [5200, 5400, 5500], high52w: 5800, low52w: 4200 },
    { ticker: "^NDX", name: "NASDAQ 100", monthReturn: -1.2, ytdReturn: 8.7, current: 19000, sparkline: [18000, 19200, 19000], high52w: 20000, low52w: 15000 },
  ],
};

// Canonical timeline payload — parses with TimelineDataSchema, ready for
// MSW handlers that want a valid default. `overrides` merges at the top level.
export function mkTimelinePayload(overrides: Record<string, unknown> = {}) {
  return {
    daily: [
      { date: "2026-01-02", total: 100000, usEquity: 55000, nonUsEquity: 15000, crypto: 3000, safeNet: 27000, liabilities: -5000 },
      { date: "2026-01-03", total: 101000, usEquity: 55500, nonUsEquity: 15200, crypto: 3100, safeNet: 27200, liabilities: -5000 },
      { date: "2026-01-06", total: 102000, usEquity: 56000, nonUsEquity: 15400, crypto: 3200, safeNet: 27400, liabilities: -5000 },
    ],
    dailyTickers: [],
    fidelityTxns: [],
    qianjiTxns: [],
    robinhoodTxns: [],
    empowerContributions: [],
    categories: CATEGORIES,
    market: MARKET,
    syncMeta: {
      last_sync: "2026-01-06T00:00:00Z",
    },
    ...overrides,
  };
}
