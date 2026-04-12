// ── Client-computed types (derived from Zod types; not API responses) ────
// These shapes are produced by `compute.ts` from the raw /timeline bundle.
// They live outside `schemas/` because that directory is reserved for Zod
// schemas validating API payloads.

import type { DailyTicker } from "@/lib/schemas";

export type MonthlyFlowPoint = { month: string; income: number; expenses: number; savingsRate: number };
export type SnapshotPoint = { date: string; total: number };
export type CategoryData = {
  name: string;
  value: number;
  lots: number;
  pct: number;
  target: number;
  deviation: number;
  isEquity: boolean;
  subtypes: { name: string; holdings: { ticker: string; value: number }[]; value: number; lots: number; pct: number }[];
  holdings: { ticker: string; value: number }[];
};
export type ApiTicker = Omit<DailyTicker, "date">;
export type ApiCategory = { name: string; value: number; pct: number; target: number; deviation: number };
export type AllocationResponse = { total: number; netWorth: number; liabilities: number; categories: ApiCategory[]; tickers: ApiTicker[] };
export type CashflowResponse = {
  incomeItems: { category: string; amount: number; count: number }[];
  expenseItems: { category: string; amount: number; count: number }[];
  totalIncome: number; totalExpenses: number; netCashflow: number;
  ccPayments: number; savingsRate: number; takehomeSavingsRate: number;
};
export type ActivityResponse = {
  buysBySymbol: { symbol: string; count: number; total: number }[];
  sellsBySymbol: { symbol: string; count: number; total: number }[];
  dividendsBySymbol: { symbol: string; count: number; total: number }[];
};
