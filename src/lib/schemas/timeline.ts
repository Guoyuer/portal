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

// ── Sparkline transform (SQLite stores sparkline as a JSON string) ───────

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
  // string (the SQLite/export storage format). Both resolve to number[] | null.
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

// QianjiTxn's `isRetirement` uses the generator's coerce_bool mode because
// SQLite stores logical booleans as INTEGER 0/1. The schema accepts
// `boolean | number` and coerces via Boolean().
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
  robinhoodTxns: z.array(RobinhoodTxnSchema),
  empowerContributions: z.array(EmpowerContributionSchema),
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
export type RobinhoodTxn = z.infer<typeof RobinhoodTxnSchema>;
export type EmpowerContribution = z.infer<typeof EmpowerContributionSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;
export type TimelineErrors = z.infer<typeof TimelineErrorsSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type CategoryMeta = z.infer<typeof CategoryMetaSchema>;
