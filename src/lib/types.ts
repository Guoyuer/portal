// Report data types — mirrors assetSnapshot's ReportData structure

export interface HoldingData {
  ticker: string;
  lots: number;
  value: number;
  pct: number;
  category: string;
  subtype: string;
}

export interface SubtypeGroup {
  name: string;
  holdings: HoldingData[];
  value: number;
  lots: number;
  pct: number;
}

export interface CategoryData {
  name: string;
  value: number;
  lots: number;
  pct: number;
  target: number;
  deviation: number;
  isEquity: boolean;
  subtypes: SubtypeGroup[];
  holdings: HoldingData[];
}

export interface CashFlowItem {
  category: string;
  amount: number;
  count: number;
}

export interface CashFlowData {
  period: string;
  incomeItems: CashFlowItem[];
  totalIncome: number;
  expenseItems: CashFlowItem[];
  totalExpenses: number;
  netCashflow: number;
  invested: number;
  creditCardPayments: number;
  savingsRate: number;
  takehomeSavingsRate: number;
}

export interface ActivitySummary {
  label: string;
  count: number;
  amount: number;
}

export interface TickerAggregation {
  symbol: string;
  trades: number;
  total: number;
}

export interface ActivityData {
  periodStart: string;
  periodEnd: string;
  summary: ActivitySummary[];
  buysByTicker: TickerAggregation[];
  dividendsByTicker: TickerAggregation[];
}

export interface AccountBalance {
  name: string;
  balance: number;
  currency: string;
  indent?: boolean;
}

export interface BalanceSheetData {
  assets: AccountBalance[];
  totalAssets: number;
  liabilities: AccountBalance[];
  totalLiabilities: number;
  netWorth: number;
}

export interface SnapshotPoint {
  date: string;
  total: number;
}

export interface MonthlyFlowPoint {
  month: string;
  income: number;
  expenses: number;
  savingsRate: number;
}

export interface ReportData {
  date: string;
  total: number;
  totalLots: number;
  goal: number;
  goalPct: number;
  netWorth: number;
  savingsRate: number;
  equityCategories: CategoryData[];
  nonEquityCategories: CategoryData[];
  cashflow: CashFlowData | null;
  activity: ActivityData | null;
  balanceSheet: BalanceSheetData | null;
  netWorthTrend: SnapshotPoint[];
  monthlyFlows: MonthlyFlowPoint[];
}
