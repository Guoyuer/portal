// ── Zod schemas — single source of truth for report data types ───────────
// All TypeScript types are derived from these schemas via z.infer.
// Only TimelineDataSchema is validated at runtime (.safeParse in use-bundle.ts).
// Sub-schemas are kept private; only the inferred types are exported.

import { z } from "zod";

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

const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
  fedRate: z.number().nullable().default(null),
  treasury10y: z.number().nullable().default(null),
  cpi: z.number().nullable().default(null),
  unemployment: z.number().nullable().default(null),
  vix: z.number().nullable().default(null),
  dxy: z.number().nullable().default(null),
  usdCny: z.number().nullable().default(null),
});

// ── Holdings Detail ──────────────────────────────────────────────────────

const StockDetailSchema = z.object({
  ticker: z.string(),
  monthReturn: z.number(),
  startValue: z.number(),
  endValue: z.number(),
  high52w: z.number().nullable().default(null),
  low52w: z.number().nullable().default(null),
  vsHigh: z.number().nullable().default(null),
});

// ── Timemachine ─────────────────────────────────────────────────────────

const DailyPointSchema = z.object({
  date: z.string(),
  total: z.number(),
  usEquity: z.number(),
  nonUsEquity: z.number(),
  crypto: z.number(),
  safeNet: z.number(),
  liabilities: z.number().default(0),
});

// ── Raw transaction schemas (bundled in /timeline) ──────────────────────

const DailyTickerSchema = z.object({
  date: z.string(),
  ticker: z.string(),
  value: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

const FidelityTxnSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  symbol: z.string(),
  amount: z.number(),
  quantity: z.number().default(0),
  price: z.number().default(0),
});

const QianjiTxnSchema = z.object({
  date: z.string(),
  type: z.string(),
  category: z.string(),
  amount: z.number(),
});

// ── Timeline (the only runtime-validated schema) ────────────────────────

export const TimelineDataSchema = z.object({
  daily: z.array(DailyPointSchema),
  dailyTickers: z.array(DailyTickerSchema).default([]),
  fidelityTxns: z.array(FidelityTxnSchema).default([]),
  qianjiTxns: z.array(QianjiTxnSchema).default([]),
  market: MarketDataSchema.nullable().default(null),
  holdingsDetail: z.array(StockDetailSchema).nullable().default(null),
  syncMeta: z.record(z.string(), z.string()).nullable().default(null),
});

// ── Inferred types (single source of truth) ─────────────────────────────

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;

// ── Client-computed types (not from Zod, defined inline) ────────────────

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

// ── Ticker price endpoint (/prices/:symbol) ───────────────────────────

export type TickerPricePoint = { date: string; close: number };
export type TickerTransaction = { runDate: string; actionType: string; quantity: number; price: number; amount: number };
export type TickerPriceResponse = { symbol: string; prices: TickerPricePoint[]; transactions: TickerTransaction[] };
