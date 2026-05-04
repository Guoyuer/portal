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

type WindowSlice = Pick<ComputedBundle, "cashflow" | "activity" | "crossCheck">;

type PreparedBundleData = Pick<
  ComputedBundle,
  | "chartDaily"
  | "dailyTickers"
  | "investmentTxns"
  | "categories"
  | "market"
  | "syncMeta"
> & {
  qianjiTxns: TimelineData["qianjiTxns"];
  tickerIndex: ReturnType<typeof buildTickerIndex>;
  dateIndex: ReturnType<typeof buildDateIndex>;
};

const EMPTY_WINDOW: WindowSlice = {
  cashflow: null,
  activity: null,
  crossCheck: null,
};

/** Window-gated computes collapse to one branch instead of per-field ternaries. */
function computeWindow(
  prepared: PreparedBundleData,
  startDate: string | null,
  snapshotDate: string | null,
): WindowSlice {
  if (!startDate || !snapshotDate) return EMPTY_WINDOW;
  return {
    cashflow: computeCashflow(prepared.qianjiTxns, startDate, snapshotDate),
    activity: computeActivity(prepared.investmentTxns, startDate, snapshotDate),
    crossCheck: computeCrossCheck(prepared.investmentTxns, prepared.qianjiTxns, startDate, snapshotDate),
  };
}

function prepareBundleData(data: TimelineData): PreparedBundleData {
  return {
    chartDaily: data.daily,
    dailyTickers: data.dailyTickers,
    qianjiTxns: data.qianjiTxns,
    investmentTxns: normalizeInvestmentTxns(
      data.fidelityTxns,
      data.robinhoodTxns,
      data.empowerContributions,
    ),
    categories: data.categories,
    market: data.market,
    syncMeta: data.syncMeta,
    tickerIndex: buildTickerIndex(data.dailyTickers),
    dateIndex: buildDateIndex(data.daily),
  };
}

function computeWindowBundle(
  prepared: PreparedBundleData,
  brushStart: number,
  brushEnd: number,
): ComputedBundle {
  const snapshot = prepared.chartDaily[brushEnd] ?? null;
  const startDate = prepared.chartDaily[brushStart]?.date ?? null;
  const snapshotDate = snapshot?.date ?? null;
  const allocation = snapshotDate
    ? computeAllocation(
        prepared.chartDaily,
        prepared.tickerIndex,
        prepared.dateIndex,
        snapshotDate,
        prepared.categories,
      )
    : null;

  return {
    chartDaily: prepared.chartDaily,
    dailyTickers: prepared.dailyTickers,
    investmentTxns: prepared.investmentTxns,
    categories: prepared.categories,
    snapshot,
    startDate,
    snapshotDate,
    allocation,
    ...computeWindow(prepared, startDate, snapshotDate),
    monthlyFlows: computeMonthlyFlows(prepared.qianjiTxns, startDate, snapshotDate),
    market: prepared.market,
    syncMeta: prepared.syncMeta,
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

  return computeWindowBundle(prepareBundleData(data), brushStart, brushEnd);
}
