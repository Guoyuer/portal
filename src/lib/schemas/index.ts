// ── Barrel: all Zod schemas + inferred types for API responses ───────────
// Single source of truth for the Worker/client type contract. Import from
// `@/lib/schemas` (client) or the relative `../../src/lib/schemas` (Worker).

export {
  SparklineSchema,
  TimelineDataSchema,
  type DailyPoint,
  type DailyTicker,
  type FidelityTxn,
  type QianjiTxn,
  type TimelineData,
  type TimelineErrors,
  type IndexReturn,
  type MarketData,
  type MarketMeta,
  type StockDetail,
  type CategoryMeta,
} from "./timeline";

export {
  EconDataSchema,
  type EconPoint,
  type EconSnapshot,
  type EconData,
} from "./econ";

export {
  TickerPriceResponseSchema,
  type TickerPricePoint,
  type TickerTransaction,
  type TickerPriceResponse,
} from "./ticker";
