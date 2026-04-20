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
  TimelineData,
} from "@/lib/schemas";
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
  computeGroupedActivity,
  computeCrossCheck,
  computeMonthlyFlows,
  normalizeInvestmentTxns,
  buildDateIndex,
  buildTickerIndex,
  type CrossCheck,
  type GroupedActivityResponse,
  type InvestmentTxn,
} from "@/lib/compute/compute";

export interface ComputedBundle {
  chartDaily: DailyPoint[];
  dailyTickers: DailyTicker[];
  qianjiTxns: QianjiTxn[];
  fidelityTxns: FidelityTxn[];
  robinhoodTxns: RobinhoodTxn[];
  empowerContributions: EmpowerContribution[];
  investmentTxns: InvestmentTxn[];
  categories: CategoryMeta[];
  snapshot: DailyPoint | null;
  startDate: string | null;
  snapshotDate: string | null;
  allocation: AllocationResponse | null;
  cashflow: CashflowResponse | null;
  activity: ActivityResponse | null;
  groupedActivity: GroupedActivityResponse | null;
  market: MarketData | null;
  holdingsDetail: StockDetail[] | null;
  crossCheck: CrossCheck | null;
  monthlyFlows: MonthlyFlowPoint[];
  syncMeta: Record<string, string> | null;
  marketError: string | null;
  holdingsError: string | null;
  txnsError: string | null;
}

const EMPTY_BUNDLE: ComputedBundle = {
  chartDaily: [],
  dailyTickers: [],
  qianjiTxns: [],
  fidelityTxns: [],
  robinhoodTxns: [],
  empowerContributions: [],
  investmentTxns: [],
  categories: [],
  snapshot: null,
  startDate: null,
  snapshotDate: null,
  allocation: null,
  cashflow: null,
  activity: null,
  groupedActivity: null,
  market: null,
  holdingsDetail: null,
  crossCheck: null,
  monthlyFlows: [],
  syncMeta: null,
  marketError: null,
  holdingsError: null,
  txnsError: null,
};

type WindowSlice = Pick<ComputedBundle, "cashflow" | "activity" | "groupedActivity" | "crossCheck">;

const EMPTY_WINDOW: WindowSlice = {
  cashflow: null,
  activity: null,
  groupedActivity: null,
  crossCheck: null,
};

/** Window-gated computes collapse to one branch instead of per-field ternaries. */
function computeWindow(
  data: TimelineData,
  investmentTxns: InvestmentTxn[],
  startDate: string | null,
  snapshotDate: string | null,
): WindowSlice {
  if (!startDate || !snapshotDate) return EMPTY_WINDOW;
  return {
    cashflow: computeCashflow(data.qianjiTxns, startDate, snapshotDate),
    activity: computeActivity(investmentTxns, startDate, snapshotDate),
    groupedActivity: computeGroupedActivity(investmentTxns, startDate, snapshotDate),
    crossCheck: computeCrossCheck(investmentTxns, data.qianjiTxns, startDate, snapshotDate),
  };
}

/** Build the full derived bundle from a parsed /timeline payload + brush window.
 *  Pure — no React, no effects. Called from `useBundle` every render; React
 *  Compiler memoizes based on argument identity. */
export function computeBundle(
  data: TimelineData | null,
  brushStart: number,
  brushEnd: number,
): ComputedBundle {
  if (!data) return EMPTY_BUNDLE;

  const investmentTxns = normalizeInvestmentTxns(
    data.fidelityTxns,
    data.robinhoodTxns,
    data.empowerContributions,
  );
  const snapshot = data.daily[brushEnd] ?? null;
  const startDate = data.daily[brushStart]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;
  const allocation = snapshotDate
    ? computeAllocation(
        data.daily,
        buildTickerIndex(data.dailyTickers),
        buildDateIndex(data.daily),
        snapshotDate,
        data.categories,
      )
    : null;

  return {
    chartDaily: data.daily,
    dailyTickers: data.dailyTickers,
    qianjiTxns: data.qianjiTxns,
    fidelityTxns: data.fidelityTxns,
    robinhoodTxns: data.robinhoodTxns,
    empowerContributions: data.empowerContributions,
    investmentTxns,
    categories: data.categories,
    snapshot,
    startDate,
    snapshotDate,
    allocation,
    ...computeWindow(data, investmentTxns, startDate, snapshotDate),
    monthlyFlows: computeMonthlyFlows(data.qianjiTxns, startDate, snapshotDate),
    // schema-nullable fields come through as T | null already; no need to coalesce
    market: data.market,
    holdingsDetail: data.holdingsDetail,
    syncMeta: data.syncMeta,
    // errors is .default({}) + optional members — convert undefined → null for the interface
    marketError: data.errors.market ?? null,
    holdingsError: data.errors.holdings ?? null,
    txnsError: data.errors.txns ?? null,
  };
}
