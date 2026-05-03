// ── Barrel: all Zod schemas + inferred types for API responses ───────────
// Single source of truth for frontend validation of R2 artifact payloads.
// Import from `@/lib/schemas` in client code and validation scripts.

export {
  TimelineDataSchema,
  type DailyPoint,
  type DailyTicker,
  type FidelityTxn,
  type QianjiTxn,
  type RobinhoodTxn,
  type EmpowerContribution,
  type TimelineData,
  type IndexReturn,
  type MarketData,
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
  TickerPricesBundleSchema,
  type TickerPricePoint,
  type TickerPricesBundle,
  type TickerTxn,
} from "./ticker";
