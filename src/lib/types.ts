// Report data types — 1:1 camelCase mirror of Python ReportData (generate_asset_snapshot/types.py)
// This is the source-of-truth contract between the Python pipeline and the TypeScript portal.

// ── Holdings & Categories ─────────────────────────────────────────────────

export interface HoldingData {
  ticker: string;
  lots: number;
  value: number;
  pct: number;
  category: string;
  subtype: string; // "broad", "growth", "other", or "" for non-equity
  costBasis: number;
  gainLoss: number;
  gainLossPct: number;
}

export interface SubtypeGroup {
  name: string; // "broad", "growth", "other"
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
  target: number; // target weight %
  deviation: number; // actual% - target%
  isEquity: boolean;
  subtypes: SubtypeGroup[];
  holdings: HoldingData[]; // flat list for non-equity
}


// ── Investment Activity ───────────────────────────────────────────────────

export interface ActivityData {
  periodStart: string;
  periodEnd: string;
  // Raw tx lists (deposits, withdrawals, buys, sells, dividends) stripped from JSON
  reinvestmentsTotal: number;
  interestTotal: number;
  foreignTaxTotal: number;
  netCashIn: number; // deposits - withdrawals
  netDeployed: number; // buys - sells
  netPassive: number; // dividends + interest - foreign_tax
  buysBySymbol: [string, number, number][]; // [symbol, trades, total]
  dividendsBySymbol: [string, number, number][]; // [symbol, count, total]
}

// ── Balance Sheet ─────────────────────────────────────────────────────────

export interface AccountBalance {
  name: string;
  balance: number;
  currency: string; // "USD" or "CNY"
}

export interface BalanceSheetData {
  investmentTotal: number; // from Fidelity positions
  accounts: AccountBalance[]; // non-Fidelity: cash, I bonds, CNY, etc.
  accountsTotal: number; // USD value of all non-Fidelity accounts
  creditCards: AccountBalance[]; // credit card balances (negative)
  totalLiabilities: number;
  totalAssets: number;
  netWorth: number;
}

// ── Cash Flow ─────────────────────────────────────────────────────────────

export interface CashFlowItem {
  category: string;
  amount: number;
  count: number;
}

export interface CashFlowData {
  period: string; // "March 2026"
  incomeItems: CashFlowItem[];
  totalIncome: number;
  expenseItems: CashFlowItem[]; // sorted by amount descending
  totalExpenses: number;
  netCashflow: number;
  invested: number; // transfers to investment accounts
  creditCardPayments: number;
  savingsRate: number; // gross, includes 401k
  takehomeSavingsRate: number; // excludes pre-tax retirement contributions
}

// ── Market Context ────────────────────────────────────────────────────────

export interface IndexReturn {
  ticker: string;
  name: string; // "S&P 500", "NASDAQ 100"
  monthReturn: number;
  ytdReturn: number;
  current: number;
}

export interface MarketData {
  indices: IndexReturn[];
  fedRate: number | null;
  treasury10y: number | null;
  cpi: number | null;
  unemployment: number | null;
  vix: number | null;
  dxy: number | null;
  usdCny: number | null;
  goldReturn: number | null;
  btcReturn: number | null;
  portfolioMonthReturn: number | null;
}

// ── Holdings Detail ───────────────────────────────────────────────────────

export interface StockDetail {
  ticker: string;
  monthReturn: number;
  startValue: number;
  endValue: number;
  peRatio: number | null;
  marketCap: number | null;
  high52w: number | null;
  low52w: number | null;
  vsHigh: number | null; // current / 52w_high - 1
  nextEarnings: string | null; // "Apr 24 (Thu)"
}

export interface HoldingsDetailData {
  topPerformers: StockDetail[]; // sorted by monthReturn desc, top 5
  bottomPerformers: StockDetail[]; // sorted by monthReturn asc, top 5
  upcomingEarnings: StockDetail[]; // stocks with earnings in next 30 days
  allStocks: StockDetail[]; // all individual stocks with data
}

// ── Annual Summary ───────────────────────────────────────────────────────

export interface AnnualCategoryTotal {
  category: string;
  amount: number;
  count: number;
}

export interface AnnualSummary {
  year: number;
  expenseByCategory: AnnualCategoryTotal[];
  totalExpenses: number;
  totalIncome: number;
}

// ── Charts ────────────────────────────────────────────────────────────────

export interface SnapshotPoint {
  date: string; // "2025-11-07"
  total: number;
}

export interface MonthlyFlowPoint {
  month: string; // "2025-11"
  income: number;
  expenses: number;
  savingsRate: number; // (income - expenses) / income * 100
}

export interface ChartData {
  netWorthTrend: SnapshotPoint[];
  monthlyFlows: MonthlyFlowPoint[];
}

// ── Cross Reconciliation ──────────────────────────────────────────────────

export interface ReconciliationMatch {
  dateQianji: string;
  dateFidelity: string;
  amount: number;
  qianjiNote: string;
  fidelityDesc: string;
}

export interface CrossReconciliationData {
  matched: ReconciliationMatch[];
  unmatchedQianji: Record<string, unknown>[];
  unmatchedFidelity: Record<string, unknown>[];
  qianjiTotal: number;
  fidelityTotal: number;
  unmatchedAmount: number;
}

// ── Portfolio Reconciliation ──────────────────────────────────────────────

export interface TierReconciliation {
  startValue: number;
  endValue: number;
  netChange: number;
  details: Record<string, unknown>;
}

export interface ReconciliationData {
  prevDate: string;
  currDate: string;
  fidelity: TierReconciliation;
  linked: TierReconciliation;
  manual: TierReconciliation;
  totalStart: number;
  totalEnd: number;
  totalChange: number;
}

// ── Full ReportData (root) ────────────────────────────────────────────────

export interface ReportData {
  // Core (always present)
  date: string;
  total: number;
  totalLots: number;
  goal: number;
  goalPct: number;
  equityCategories: CategoryData[];
  nonEquityCategories: CategoryData[];

  // Investment activity (if Fidelity history available)
  activity: ActivityData | null;

  // Portfolio reconciliation (if previous snapshot exists)
  reconciliation: ReconciliationData | null;

  // Personal finance (if Qianji available)
  balanceSheet: BalanceSheetData | null;
  cashflow: CashFlowData | null;
  crossReconciliation: CrossReconciliationData | null;

  // Market context (if APIs available)
  market: MarketData | null;
  holdingsDetail: HoldingsDetailData | null;

  // Charts (if historical data available)
  chartData: ChartData | null;

  // Annual summary
  annualSummary: AnnualSummary | null;

  // Pipeline metadata (file timestamps)
  metadata?: {
    generatedAt: string;
    positionsDate: string;
    historyDate: string;
    qianjiDate: string;
  };
}
