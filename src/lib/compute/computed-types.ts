// ── Client-computed types (derived from Zod types; not API responses) ────
// These shapes are produced by `compute.ts` from the raw /timeline bundle.
// They live outside `schemas/` because that directory is reserved for Zod
// schemas validating API payloads.

import type { DailyTicker } from "@/lib/schemas/timeline";

export type SourceKind = "fidelity" | "robinhood" | "401k";

export type MonthlyFlowPoint = { month: string; income: number; expenses: number; savings: number; savingsRate: number };
export type ApiTicker = Omit<DailyTicker, "date">;
export type ApiCategory = { name: string; value: number; pct: number; target: number };
export type AllocationResponse = { total: number; netWorth: number; categories: ApiCategory[]; tickers: ApiTicker[] };
export type CashflowResponse = {
  incomeItems: { category: string; amount: number; count: number }[];
  expenseItems: { category: string; amount: number; count: number }[];
  totalIncome: number; totalExpenses: number; netCashflow: number;
  ccPayments: number; savingsRate: number; takehomeSavingsRate: number;
};

export type ActivityTicker = {
  ticker: string;
  count: number;
  total: number;
  groupKey?: string;
  sources: Array<SourceKind>;
};

export type ActivityResponse = {
  buysBySymbol: ActivityTicker[];
  sellsBySymbol: ActivityTicker[];
  dividendsBySymbol: ActivityTicker[];
};
