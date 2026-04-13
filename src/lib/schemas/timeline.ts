// ── Timeline endpoint schemas (/timeline) ────────────────────────────────
// Runtime + compile-time types for the single /timeline payload. Shared
// between the Next.js client and the Cloudflare Worker output-validation
// layer. D1 views shape the rows to match these exact schemas.

import { z } from "zod";

// ── Sparkline transform (D1 stores sparkline as a JSON string) ───────────
// Used by IndexReturnSchema below but also re-exported via the barrel so
// both client and Worker can reuse the same transform.

export const SparklineSchema = z
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

const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
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
  quantity: z.number(),
  price: z.number(),
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

// ── Inferred types ──────────────────────────────────────────────────────

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type CategoryMeta = z.infer<typeof CategoryMetaSchema>;
