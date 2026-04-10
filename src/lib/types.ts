// Re-export all types from schema.ts (the single source of truth).
// This file exists so existing imports from "@/lib/types" keep working.
export type {
  IndexReturn,
  MarketData,
  StockDetail,
  HoldingsDetailData,
  MonthlyFlowPoint,
  SnapshotPoint,
  AnnualCategoryTotal,
  AnnualSummary,
  CategoryData,
  ApiTicker,
  ApiCategory,
  AllocationResponse,
  CashflowResponse,
  ActivitySymbol,
  ActivityResponse,
} from "./schema";
