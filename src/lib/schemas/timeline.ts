// ── Timeline endpoint schemas (/timeline) ────────────────────────────────
// Runtime + compile-time types for the single /timeline payload. R2 artifacts
// are exported to match these exact schemas; the Next.js client is the drift
// checkpoint.

import { z } from "zod";

import {
  AllocationRowSchema,
  EmpowerContributionSchema,
  FidelityTxnSchema,
  QianjiTxnSchema,
  RobinhoodTxnSchema,
  TickerDetailSchema,
} from "./_generated";

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
