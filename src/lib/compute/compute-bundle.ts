import type {
  CategoryMeta,
  MarketData,
  DailyPoint,
  DailyTicker,
  TimelineData,
} from "@/lib/schemas/timeline";
import type {
  AllocationResponse,
  CashflowResponse,
  ActivityResponse,
  MonthlyFlowPoint,
} from "@/lib/compute/computed-types";
import {
  computeAllocation,
  computeCashflow,
  computeActivity,
  computeCrossCheck,
  computeMonthlyFlows,
  normalizeInvestmentTxns,
  buildDateIndex,
  buildTickerIndex,
  type CrossCheck,
  type InvestmentTxn,
} from "@/lib/compute/compute";

export interface ComputedBundle {
  chartDaily: DailyPoint[];
  dailyTickers: DailyTicker[];
  investmentTxns: InvestmentTxn[];
  categories: CategoryMeta[];
  snapshot: DailyPoint | null;
  startDate: string | null;
  snapshotDate: string | null;
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  market: MarketData | null;
  crossCheck: CrossCheck | null;
  monthlyFlows: MonthlyFlowPoint[];
  syncMeta: TimelineData["syncMeta"] | null;
}

const EMPTY_BUNDLE: ComputedBundle = {
  chartDaily: [],
  dailyTickers: [],
  investmentTxns: [],
  categories: [],
  snapshot: null,
  startDate: null,
  snapshotDate: null,
  allocation: null,
  cashflow: null,
  activity: null,
  market: null,
  crossCheck: null,
  monthlyFlows: [],
  syncMeta: null,
};

/** Build the full derived bundle from a parsed /timeline payload + brush window.
 *  Pure — no React, no effects. Called from `useBundle` every render; React
 *  Compiler memoizes based on argument identity. */
export function computeBundle(
  data: TimelineData | null,
  brushStart: number,
  brushEnd: number,
): ComputedBundle {
  if (!data) return EMPTY_BUNDLE;

  const chartDaily = data.daily;
  const dailyTickers = data.dailyTickers;
  const qianjiTxns = data.qianjiTxns;
  const investmentTxns = normalizeInvestmentTxns(
    data.fidelityTxns,
    data.robinhoodTxns,
    data.empowerContributions,
  );
  const snapshot = chartDaily[brushEnd] ?? null;
  const startDate = chartDaily[brushStart]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;
  const allocation = snapshotDate
    ? computeAllocation(
        chartDaily,
        buildTickerIndex(dailyTickers),
        buildDateIndex(chartDaily),
        snapshotDate,
        data.categories,
      )
    : null;
  const windowed = startDate && snapshotDate
    ? {
        cashflow: computeCashflow(qianjiTxns, startDate, snapshotDate),
        activity: computeActivity(investmentTxns, startDate, snapshotDate),
        crossCheck: computeCrossCheck(investmentTxns, qianjiTxns, startDate, snapshotDate),
      }
    : {
        cashflow: null,
        activity: null,
        crossCheck: null,
      };

  return {
    chartDaily,
    dailyTickers,
    investmentTxns,
    categories: data.categories,
    snapshot,
    startDate,
    snapshotDate,
    allocation,
    ...windowed,
    monthlyFlows: computeMonthlyFlows(qianjiTxns, startDate, snapshotDate),
    market: data.market,
    syncMeta: data.syncMeta,
  };
}
