// ── Ticker price endpoint schemas (/prices) ──────────────────────────────
// Runtime + compile-time types for the lazy prices bundle and each ticker's
// chart payload.

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
  prices: z.array(TickerPricePointSchema),
  transactions: z.array(TickerTxnSchema),
});

export const TickerPricesBundleSchema = z.record(z.string(), TickerPriceResponseSchema);

export type TickerTxn = z.infer<typeof TickerTxnSchema>;
export type TickerPricesBundle = z.infer<typeof TickerPricesBundleSchema>;
