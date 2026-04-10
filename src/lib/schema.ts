// ── Zod schemas — single source of truth for report data types ───────────
// All TypeScript types are derived from these schemas via z.infer.
// Runtime validation + compile-time types from one definition.

import { z } from "zod";

// ── Cash Flow ────────────────────────────────────────────────────────────

const CashFlowItemSchema = z.object({
  category: z.string(),
  amount: z.number(),
  count: z.number(),
});

// ── Market Context ───────────────────────────────────────────────────────

const IndexReturnSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  monthReturn: z.number(),
  ytdReturn: z.number(),
  current: z.number(),
  sparkline: z.array(z.number()).nullable().default(null),
  high52w: z.number().nullable().default(null),
  low52w: z.number().nullable().default(null),
});

export const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
  fedRate: z.number().nullable().default(null),
  treasury10y: z.number().nullable().default(null),
  cpi: z.number().nullable().default(null),
  unemployment: z.number().nullable().default(null),
  vix: z.number().nullable().default(null),
  dxy: z.number().nullable().default(null),
  usdCny: z.number().nullable().default(null),
  goldReturn: z.number().nullable().default(null),
  btcReturn: z.number().nullable().default(null),
  portfolioMonthReturn: z.number().nullable().default(null),
});

// ── Holdings Detail ──────────────────────────────────────────────────────

const StockDetailSchema = z.object({
  ticker: z.string(),
  monthReturn: z.number(),
  startValue: z.number(),
  endValue: z.number(),
  peRatio: z.number().nullable().default(null),
  marketCap: z.number().nullable().default(null),
  high52w: z.number().nullable().default(null),
  low52w: z.number().nullable().default(null),
  vsHigh: z.number().nullable().default(null),
  nextEarnings: z.string().nullable().default(null),
});

const HoldingsDetailDataSchema = z.object({
  allStocks: z.array(StockDetailSchema),
});

// ── Timemachine ─────────────────────────────────────────────────────────

export const DailyPointSchema = z.object({
  date: z.string(),
  total: z.number(),
  usEquity: z.number(),
  nonUsEquity: z.number(),
  crypto: z.number(),
  safeNet: z.number(),
  liabilities: z.number().default(0),
});

// ── Raw transaction schemas (bundled in /timeline) ──────────────────────

export const DailyTickerSchema = z.object({
  date: z.string(),
  ticker: z.string(),
  value: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

export const FidelityTxnSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  symbol: z.string(),
  amount: z.number(),
});

export const QianjiTxnSchema = z.object({
  date: z.string(),
  type: z.string(),
  category: z.string(),
  amount: z.number(),
});

export const TimelineDataSchema = z.object({
  daily: z.array(DailyPointSchema),
  dailyTickers: z.array(DailyTickerSchema).default([]),
  fidelityTxns: z.array(FidelityTxnSchema).default([]),
  qianjiTxns: z.array(QianjiTxnSchema).default([]),
  market: MarketDataSchema.nullable().default(null),
  holdingsDetail: HoldingsDetailDataSchema.nullable().default(null),
  syncMeta: z.record(z.string(), z.string()).nullable().default(null),
});

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;

// ── API Response Schemas ──────────────────────────────────────────────────

export const ApiTickerSchema = z.object({
  ticker: z.string(),
  value: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

export const ApiCategorySchema = z.object({
  name: z.string(),
  value: z.number(),
  pct: z.number(),
  target: z.number(),
  deviation: z.number(),
});

export const AllocationResponseSchema = z.object({
  total: z.number(),
  netWorth: z.number(),
  liabilities: z.number(),
  categories: z.array(ApiCategorySchema),
  tickers: z.array(ApiTickerSchema),
});

export const CashflowResponseSchema = z.object({
  incomeItems: z.array(CashFlowItemSchema),
  expenseItems: z.array(CashFlowItemSchema),
  totalIncome: z.number(),
  totalExpenses: z.number(),
  netCashflow: z.number(),
  ccPayments: z.number(),
  savingsRate: z.number(),
  takehomeSavingsRate: z.number(),
});

export const ActivitySymbolSchema = z.object({
  symbol: z.string(),
  count: z.number(),
  total: z.number(),
});

export const ActivityResponseSchema = z.object({
  buysBySymbol: z.array(ActivitySymbolSchema),
  sellsBySymbol: z.array(ActivitySymbolSchema),
  dividendsBySymbol: z.array(ActivitySymbolSchema),
});

// ── Inferred types (single source of truth) ─────────────────────────────

export type CashFlowItem = z.infer<typeof CashFlowItemSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type HoldingsDetailData = z.infer<typeof HoldingsDetailDataSchema>;
export type MonthlyFlowPoint = { month: string; income: number; expenses: number; savingsRate: number };
export type SnapshotPoint = { date: string; total: number };
export type AnnualCategoryTotal = { category: string; amount: number; count: number };
export type AnnualSummary = {
  year: number;
  expenseByCategory: AnnualCategoryTotal[];
  totalExpenses: number;
  totalIncome: number;
  takehomeSavingsRate?: number;
};
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
export type ApiTicker = z.infer<typeof ApiTickerSchema>;
export type ApiCategory = z.infer<typeof ApiCategorySchema>;
export type AllocationResponse = z.infer<typeof AllocationResponseSchema>;
export type CashflowResponse = z.infer<typeof CashflowResponseSchema>;
export type ActivitySymbol = z.infer<typeof ActivitySymbolSchema>;
export type ActivityResponse = z.infer<typeof ActivityResponseSchema>;
