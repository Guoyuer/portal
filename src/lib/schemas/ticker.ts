// ── Ticker price endpoint schemas (/prices/:symbol) ──────────────────────
// Runtime + compile-time types for the on-demand ticker detail payload.

import { z } from "zod";

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

export type TickerPricePoint = z.infer<typeof TickerPricePointSchema>;
export type TickerTransaction = z.infer<typeof TickerTransactionSchema>;
