// ── Econ endpoint schemas (/econ) ────────────────────────────────────────
// Runtime validation + compile-time types from one definition.

import { z } from "zod";

const EconPointSchema = z.object({
  date: z.string(),
  value: z.number(),
});

const EconPointsSchema = z.array(EconPointSchema);

const EconSnapshotSchema = z.object({
  fedFundsRate: z.number().optional(),
  treasury10y: z.number().optional(),
  treasury2y: z.number().optional(),
  spread2s10s: z.number().optional(),
  cpiYoy: z.number().optional(),
  coreCpiYoy: z.number().optional(),
  unemployment: z.number().optional(),
  vix: z.number().optional(),
  dxy: z.number().optional(),
  oilWti: z.number().optional(),
  usdCny: z.number().optional(),
});

export const EconDataSchema = z.object({
  generatedAt: z.string(),
  snapshot: EconSnapshotSchema,
  series: z.record(z.string(), EconPointsSchema),
});

export type EconPoint = z.infer<typeof EconPointSchema>;
export type EconSnapshot = z.infer<typeof EconSnapshotSchema>;
export type EconData = z.infer<typeof EconDataSchema>;
