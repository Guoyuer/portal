// ── Ticker price endpoint schemas (/prices/:symbol) ──────────────────────
// Runtime + compile-time types for the on-demand ticker detail payload.

import { z } from "zod";

const TickerPricePointSchema = z.object({
  date: z.string(),
  close: z.number(),
});

const TickerTxnSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  quantity: z.number(),
  price: z.number(),
  amount: z.number(),
});

export const TickerPriceResponseSchema = z.object({
  symbol: z.string(),
  prices: z.array(TickerPricePointSchema).default([]),
  transactions: z.array(TickerTxnSchema).default([]),
});

export type TickerPricePoint = z.infer<typeof TickerPricePointSchema>;
export type TickerTxn = z.infer<typeof TickerTxnSchema>;
