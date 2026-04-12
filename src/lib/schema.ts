// ── Zod schemas — single source of truth for API response types ───────────
// Imported by both the Next.js client (runtime + compile-time) and the
// Cloudflare Worker (output validation before Response.json).
//
// D1 views shape payloads to match these schemas directly; the Worker is a
// thin passthrough. The only transform remaining is `SparklineSchema`, which
// parses the JSON string stored in D1 into an array on the client.

import { z } from "zod";

// ── Sparkline transform (D1 stores sparkline as a JSON string) ───────────

const SparklineSchema = z
  .string()
  .transform((s, ctx) => {
    try {
      return JSON.parse(s) as unknown;
    } catch {
      ctx.addIssue({ code: "custom", message: "Invalid sparkline JSON" });
      return z.NEVER;
    }
  })
  .pipe(z.array(z.number()))
  .nullable();

// ── Market Context ───────────────────────────────────────────────────────

const IndexReturnSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  monthReturn: z.number(),
  ytdReturn: z.number(),
  current: z.number(),
  // Accept either an already-parsed array (e.g. mock API fixtures) or a JSON
  // string (the D1 storage format). Both resolve to number[] | null.
  sparkline: z.union([SparklineSchema, z.array(z.number()).nullable()]).default(null),
  high52w: z.number().nullable().default(null),
  low52w: z.number().nullable().default(null),
});

// Scalar macro indicators — one row produced by v_market_meta.
const MarketMetaSchema = z.object({
  fedRate: z.number().nullable().default(null),
  treasury10y: z.number().nullable().default(null),
  cpi: z.number().nullable().default(null),
  unemployment: z.number().nullable().default(null),
  vix: z.number().nullable().default(null),
  dxy: z.number().nullable().default(null),
  usdCny: z.number().nullable().default(null),
});

const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
  meta: MarketMetaSchema,
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
  // SQLite returns INTEGER; accept 0/1 and coerce to boolean. Mocks/tests may
  // pass a bare boolean. Absent → false.
  isRetirement: z
    .union([z.boolean(), z.number()])
    .optional()
    .transform((v) => (v === undefined ? false : Boolean(v))),
});

// ── Category metadata (target weights + display order from pipeline) ────

const CategoryMetaSchema = z.object({
  key: z.string(),
  name: z.string(),
  displayOrder: z.number(),
  targetPct: z.number(),
});

// ── Per-section errors (populated when an optional view fails) ──────────

const TimelineErrorsSchema = z
  .object({
    market: z.string().optional(),
    holdings: z.string().optional(),
    txns: z.string().optional(),
  })
  .default({});

// ── Timeline ────────────────────────────────────────────────────────────

export const TimelineDataSchema = z.object({
  daily: z.array(DailyPointSchema),
  dailyTickers: z.array(DailyTickerSchema).default([]),
  fidelityTxns: z.array(FidelityTxnSchema).default([]),
  qianjiTxns: z.array(QianjiTxnSchema).default([]),
  categories: z.array(CategoryMetaSchema),
  market: MarketDataSchema.nullable().default(null),
  holdingsDetail: z.array(StockDetailSchema).nullable().default(null),
  syncMeta: z.record(z.string(), z.string()).nullable().default(null),
  errors: TimelineErrorsSchema,
});

// ── Ticker price endpoint (/prices/:symbol) ──────────────────────────────

const TickerPricePointSchema = z.object({
  date: z.string(),
  close: z.number(),
});

const TickerTransactionSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  quantity: z.number(),
  price: z.number(),
  amount: z.number(),
});

export const TickerPriceResponseSchema = z.object({
  symbol: z.string(),
  prices: z.array(TickerPricePointSchema).default([]),
  transactions: z.array(TickerTransactionSchema).default([]),
});

// ── Inferred types (single source of truth) ─────────────────────────────

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;
export type TimelineErrors = z.infer<typeof TimelineErrorsSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type MarketMeta = z.infer<typeof MarketMetaSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type TickerPricePoint = z.infer<typeof TickerPricePointSchema>;
export type TickerTransaction = z.infer<typeof TickerTransactionSchema>;
export type TickerPriceResponse = z.infer<typeof TickerPriceResponseSchema>;
export type CategoryMeta = z.infer<typeof CategoryMetaSchema>;

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
