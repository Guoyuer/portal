// ── Timeline endpoint schemas (/timeline) ────────────────────────────────
// Runtime + compile-time types for the single /timeline payload. R2 artifacts
// are exported to match these exact schemas; the Next.js client is the drift
// checkpoint.

import { z } from "zod";

// ── Market Context ───────────────────────────────────────────────────────

const IndexReturnSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  monthReturn: z.number(),
  ytdReturn: z.number(),
  current: z.number(),
  sparkline: z.array(z.number()),
  high52w: z.number(),
  low52w: z.number(),
});

const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
});

// ── Timemachine ─────────────────────────────────────────────────────────

const DailyPointSchema = z.object({
  date: z.string(),
  total: z.number(),
  usEquity: z.number(),
  nonUsEquity: z.number(),
  crypto: z.number(),
  safeNet: z.number(),
  liabilities: z.number(),
});

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

// ── Raw transaction schemas (bundled in /timeline) ──────────────────────

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
  accountTo: z.string(),
  isRetirement: z.boolean(),
});

const RobinhoodTxnSchema = z.object({
  txnDate: z.string(),
  actionKind: z.string(),
  ticker: z.string(),
  quantity: z.number(),
  amountUsd: z.number(),
});

const EmpowerContributionSchema = z.object({
  date: z.string(),
  amount: z.number(),
  ticker: z.string(),
});

// ── Category metadata (target weights + display order from pipeline) ────

const CategoryMetaSchema = z.object({
  key: z.string(),
  name: z.string(),
  displayOrder: z.number(),
  targetPct: z.number(),
});

// ── Timeline ────────────────────────────────────────────────────────────

export const TimelineDataSchema = z.object({
  daily: z.array(DailyPointSchema),
  dailyTickers: z.array(DailyTickerSchema),
  fidelityTxns: z.array(FidelityTxnSchema),
  qianjiTxns: z.array(QianjiTxnSchema),
  robinhoodTxns: z.array(RobinhoodTxnSchema),
  empowerContributions: z.array(EmpowerContributionSchema),
  categories: z.array(CategoryMetaSchema),
  market: MarketDataSchema,
  syncMeta: z.record(z.string(), z.string()),
});

// ── Inferred types ──────────────────────────────────────────────────────

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type RobinhoodTxn = z.infer<typeof RobinhoodTxnSchema>;
export type EmpowerContribution = z.infer<typeof EmpowerContributionSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type CategoryMeta = z.infer<typeof CategoryMetaSchema>;
