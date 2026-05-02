"use client";

import type {
  CategoryMeta,
  MarketData,
  StockDetail,
  DailyPoint,
  DailyTicker,
  QianjiTxn,
  FidelityTxn,
  RobinhoodTxn,
  EmpowerContribution,
} from "@/lib/schemas";
import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  MonthlyFlowPoint,
} from "@/lib/compute/computed-types";
import type {
  CrossCheck,
  GroupedActivityResponse,
  InvestmentTxn,
} from "@/lib/compute/compute";
import { computeBundle } from "@/lib/compute/compute-bundle";
import { useTimelineData } from "./use-timeline-data";
import { useBrushRange } from "./use-brush-range";

export interface BundleState {
  chartDaily: DailyPoint[];
  dailyTickers: DailyTicker[];
  qianjiTxns: QianjiTxn[];
  fidelityTxns: FidelityTxn[];
  robinhoodTxns: RobinhoodTxn[];
  empowerContributions: EmpowerContribution[];
  investmentTxns: InvestmentTxn[];
  categories: CategoryMeta[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  snapshot: DailyPoint | null;
  startDate: string | null;
  snapshotDate: string | null;
  brushStart: number;
  brushEnd: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
  loading: boolean;
  error: string | null;
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  groupedActivity: GroupedActivityResponse | null;
  market: MarketData | null;
  holdingsDetail: StockDetail[] | null;
  crossCheck: CrossCheck | null;
  monthlyFlows: MonthlyFlowPoint[];
  syncMeta: Record<string, string> | null;
}

/** Finance dashboard's single data entry point. Orchestrates three layers:
 *  fetch+parse (`useTimelineData`), brush window state (`useBrushRange`),
 *  and the pure compute pipeline (`computeBundle`). */
export function useBundle(): BundleState {
  const { data, loading, error } = useTimelineData();
  const brush = useBrushRange(data);
  const computed = computeBundle(data, brush.brushStart, brush.brushEnd);
  return { ...computed, ...brush, loading, error };
}
