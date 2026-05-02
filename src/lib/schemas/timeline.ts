// ── Timeline endpoint schemas (/timeline) ────────────────────────────────
// Runtime + compile-time types for the single /timeline payload. R2 artifacts
// are exported to match these exact schemas; the Next.js client is the drift
// checkpoint.

import { z } from "zod";

import {
  AllocationRowSchema,
  EmpowerContributionSchema as GeneratedEmpowerContributionSchema,
  FidelityTxnSchema as GeneratedFidelityTxnSchema,
  QianjiTxnSchema as GeneratedQianjiTxnSchema,
  RobinhoodTxnSchema as GeneratedRobinhoodTxnSchema,
  TickerDetailSchema,
} from "./_generated";

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

// ── Timemachine (derived from generated AllocationRow) ─────────────────
// DailyPoint is AllocationRow minus the nested `tickers` — those rows are
// flattened into a top-level `dailyTickers` array by the exporter.
const DailyPointSchema = AllocationRowSchema.omit({ tickers: true });

// ── Raw transaction schemas (bundled in /timeline) ──────────────────────
// DailyTicker = TickerDetail + `date` (denormalized from the parent row
// when flattened for the API).
const DailyTickerSchema = TickerDetailSchema.extend({
  date: z.string(),
});

// FidelityTxn is the pure 1:1 subset/rename of FidelityTransaction — use
// the generated schema directly.
const FidelityTxnSchema = GeneratedFidelityTxnSchema;

// QianjiTxn is exported as endpoint JSON, so logical booleans must already be
// JSON booleans before they reach the frontend schema.
const QianjiTxnSchema = GeneratedQianjiTxnSchema;

// RobinhoodTxn — direct 1:1 projection from the generated schema.
const RobinhoodTxnSchema = GeneratedRobinhoodTxnSchema;

// EmpowerContribution — direct 1:1 projection from the generated schema.
const EmpowerContributionSchema = GeneratedEmpowerContributionSchema;

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
  holdingsDetail: z.array(StockDetailSchema),
  syncMeta: z.record(z.string(), z.string()).nullable().default(null),
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
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type CategoryMeta = z.infer<typeof CategoryMetaSchema>;
