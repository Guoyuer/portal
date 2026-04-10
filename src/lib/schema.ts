// ── Zod schemas — single source of truth for report data types ───────────
// All TypeScript types are derived from these schemas via z.infer.
// Runtime validation + compile-time types from one definition.

import { z } from "zod";

// ── Holdings & Categories ────────────────────────────────────────────────

const HoldingDataSchema = z.object({
  ticker: z.string(),
  lots: z.number(),
  value: z.number(),
  pct: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

const SubtypeGroupSchema = z.object({
  name: z.string(),
  holdings: z.array(HoldingDataSchema),
  value: z.number(),
  lots: z.number(),
  pct: z.number(),
});

const CategoryDataSchema = z.object({
  name: z.string(),
  value: z.number(),
  lots: z.number(),
  pct: z.number(),
  target: z.number(),
  deviation: z.number(),
  isEquity: z.boolean(),
  subtypes: z.array(SubtypeGroupSchema).default([]),
  holdings: z.array(HoldingDataSchema).default([]),
});

// ── Investment Activity ──────────────────────────────────────────────────

const ActivityDataSchema = z.object({
  periodStart: z.string(),
  periodEnd: z.string(),
  reinvestmentsTotal: z.number(),
  interestTotal: z.number(),
  foreignTaxTotal: z.number(),
  netCashIn: z.number(),
  netDeployed: z.number(),
  netPassive: z.number(),
  buysBySymbol: z.array(z.tuple([z.string(), z.number(), z.number()])),
  dividendsBySymbol: z.array(z.tuple([z.string(), z.number(), z.number()])),
});

// ── Balance Sheet ────────────────────────────────────────────────────────

const BalanceSheetDataSchema = z.object({
  totalAssets: z.number(),
  totalLiabilities: z.number(),
  netWorth: z.number(),
});

// ── Cash Flow ────────────────────────────────────────────────────────────

const CashFlowItemSchema = z.object({
  category: z.string(),
  amount: z.number(),
  count: z.number(),
});

const CashFlowDataSchema = z.object({
  period: z.string(),
  incomeItems: z.array(CashFlowItemSchema),
  totalIncome: z.number(),
  expenseItems: z.array(CashFlowItemSchema),
  totalExpenses: z.number(),
  netCashflow: z.number(),
  invested: z.number(),
  creditCardPayments: z.number(),
  savingsRate: z.number(),
  takehomeSavingsRate: z.number(),
});

// ── Market Context ───────────────────────────────────────────────────────

const IndexReturnSchema = z.object({
  ticker: z.string(),
  name: z.string(),
  monthReturn: z.number(),
  ytdReturn: z.number(),
  current: z.number(),
  sparkline: z.array(z.number()).nullable().default(null),
  high52w: z.number().nullable().default(null),
  low52w: z.number().nullable().default(null),
});

export const MarketDataSchema = z.object({
  indices: z.array(IndexReturnSchema),
  fedRate: z.number().nullable().default(null),
  treasury10y: z.number().nullable().default(null),
  cpi: z.number().nullable().default(null),
  unemployment: z.number().nullable().default(null),
  vix: z.number().nullable().default(null),
  dxy: z.number().nullable().default(null),
  usdCny: z.number().nullable().default(null),
  goldReturn: z.number().nullable().default(null),
  btcReturn: z.number().nullable().default(null),
  portfolioMonthReturn: z.number().nullable().default(null),
});

// ── Holdings Detail ──────────────────────────────────────────────────────

const StockDetailSchema = z.object({
  ticker: z.string(),
  monthReturn: z.number(),
  startValue: z.number(),
  endValue: z.number(),
  peRatio: z.number().nullable(),
  marketCap: z.number().nullable(),
  high52w: z.number().nullable(),
  low52w: z.number().nullable(),
  vsHigh: z.number().nullable(),
  nextEarnings: z.string().nullable(),
});

const HoldingsDetailDataSchema = z.object({
  allStocks: z.array(StockDetailSchema),
});

// ── Annual Summary ───────────────────────────────────────────────────────

const AnnualCategoryTotalSchema = z.object({
  category: z.string(),
  amount: z.number(),
  count: z.number(),
});

const AnnualSummarySchema = z.object({
  year: z.number(),
  expenseByCategory: z.array(AnnualCategoryTotalSchema),
  totalExpenses: z.number(),
  totalIncome: z.number(),
  takehomeSavingsRate: z.number().optional(),
});

// ── Charts ───────────────────────────────────────────────────────────────

const SnapshotPointSchema = z.object({
  date: z.string(),
  total: z.number(),
});

const MonthlyFlowPointSchema = z.object({
  month: z.string(),
  income: z.number(),
  expenses: z.number(),
  savingsRate: z.number(),
});

const ChartDataSchema = z.object({
  netWorthTrend: z.array(SnapshotPointSchema).default([]),
  monthlyFlows: z.array(MonthlyFlowPointSchema).default([]),
});

// ── Cross Reconciliation ─────────────────────────────────────────────────

const ReconciliationMatchSchema = z.object({
  dateQianji: z.string(),
  dateFidelity: z.string(),
  amount: z.number(),
  qianjiNote: z.string(),
  fidelityDesc: z.string(),
});

const CrossReconciliationDataSchema = z.object({
  matched: z.array(ReconciliationMatchSchema),
  unmatchedQianji: z.array(z.record(z.string(), z.unknown())),
  unmatchedFidelity: z.array(z.record(z.string(), z.unknown())),
  qianjiTotal: z.number(),
  fidelityTotal: z.number(),
  unmatchedAmount: z.number(),
});

// ── Portfolio Reconciliation ─────────────────────────────────────────────

const TierReconciliationSchema = z.object({
  startValue: z.number(),
  endValue: z.number(),
  netChange: z.number(),
  details: z.record(z.string(), z.unknown()),
});

const ReconciliationDataSchema = z.object({
  prevDate: z.string(),
  currDate: z.string(),
  fidelity: TierReconciliationSchema,
  linked: TierReconciliationSchema,
  manual: TierReconciliationSchema,
  totalStart: z.number(),
  totalEnd: z.number(),
  totalChange: z.number(),
});

// ── Timemachine ─────────────────────────────────────────────────────────

export const DailyPointSchema = z.object({
  date: z.string(),
  total: z.number(),
  usEquity: z.number(),
  nonUsEquity: z.number(),
  crypto: z.number(),
  safeNet: z.number(),
  liabilities: z.number().default(0),
});

// ── Raw transaction schemas (bundled in /timeline) ──────────────────────

export const DailyTickerSchema = z.object({
  date: z.string(),
  ticker: z.string(),
  value: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

export const FidelityTxnSchema = z.object({
  runDate: z.string(),
  actionType: z.string(),
  symbol: z.string(),
  amount: z.number(),
});

export const QianjiTxnSchema = z.object({
  date: z.string(),
  type: z.string(),
  category: z.string(),
  amount: z.number(),
});

export const TimelineDataSchema = z.object({
  daily: z.array(DailyPointSchema),
  dailyTickers: z.array(DailyTickerSchema).default([]),
  fidelityTxns: z.array(FidelityTxnSchema).default([]),
  qianjiTxns: z.array(QianjiTxnSchema).default([]),
  market: MarketDataSchema.nullable().default(null),
  holdingsDetail: HoldingsDetailDataSchema.nullable().default(null),
});

export type DailyPoint = z.infer<typeof DailyPointSchema>;
export type DailyTicker = z.infer<typeof DailyTickerSchema>;
export type FidelityTxn = z.infer<typeof FidelityTxnSchema>;
export type QianjiTxn = z.infer<typeof QianjiTxnSchema>;
export type TimelineData = z.infer<typeof TimelineDataSchema>;

// ── API Response Schemas ──────────────────────────────────────────────────

export const ApiTickerSchema = z.object({
  ticker: z.string(),
  value: z.number(),
  category: z.string(),
  subtype: z.string(),
  costBasis: z.number(),
  gainLoss: z.number(),
  gainLossPct: z.number(),
});

export const ApiCategorySchema = z.object({
  name: z.string(),
  value: z.number(),
  pct: z.number(),
  target: z.number(),
  deviation: z.number(),
});

export const AllocationResponseSchema = z.object({
  total: z.number(),
  netWorth: z.number(),
  liabilities: z.number(),
  categories: z.array(ApiCategorySchema),
  tickers: z.array(ApiTickerSchema),
});

export const CashflowResponseSchema = z.object({
  incomeItems: z.array(CashFlowItemSchema),
  expenseItems: z.array(CashFlowItemSchema),
  totalIncome: z.number(),
  totalExpenses: z.number(),
  netCashflow: z.number(),
  ccPayments: z.number(),
  savingsRate: z.number(),
  takehomeSavingsRate: z.number(),
});

export const ActivitySymbolSchema = z.object({
  symbol: z.string(),
  count: z.number(),
  total: z.number(),
});

export const ActivityResponseSchema = z.object({
  buysBySymbol: z.array(ActivitySymbolSchema),
  sellsBySymbol: z.array(ActivitySymbolSchema),
  dividendsBySymbol: z.array(ActivitySymbolSchema),
});

export type ApiTicker = z.infer<typeof ApiTickerSchema>;
export type ApiCategory = z.infer<typeof ApiCategorySchema>;
export type AllocationResponse = z.infer<typeof AllocationResponseSchema>;
export type CashflowResponse = z.infer<typeof CashflowResponseSchema>;
export type ActivitySymbol = z.infer<typeof ActivitySymbolSchema>;
export type ActivityResponse = z.infer<typeof ActivityResponseSchema>;

// ── Full ReportData (root) ───────────────────────────────────────────────

export const ReportDataSchema = z.object({
  date: z.string(),
  total: z.number(),
  totalLots: z.number(),
  goal: z.number(),
  goalPct: z.number(),
  equityCategories: z.array(CategoryDataSchema),
  nonEquityCategories: z.array(CategoryDataSchema),

  activity: ActivityDataSchema.nullable().default(null),
  reconciliation: ReconciliationDataSchema.nullable().default(null),
  balanceSheet: BalanceSheetDataSchema.nullable().default(null),
  cashflow: CashFlowDataSchema.nullable().default(null),
  crossReconciliation: CrossReconciliationDataSchema.nullable().default(null),
  market: MarketDataSchema.nullable().default(null),
  holdingsDetail: HoldingsDetailDataSchema.nullable().default(null),
  chartData: ChartDataSchema.nullable().default(null),
  annualSummary: AnnualSummarySchema.nullable().default(null),

  metadata: z.object({
    generatedAt: z.string(),
    positionsDate: z.string(),
    historyDate: z.string(),
    qianjiDate: z.string(),
  }).optional(),
});

// ── Inferred types (single source of truth) ─────────────────────────────

export type HoldingData = z.infer<typeof HoldingDataSchema>;
export type SubtypeGroup = z.infer<typeof SubtypeGroupSchema>;
export type CategoryData = z.infer<typeof CategoryDataSchema>;
export type ActivityData = z.infer<typeof ActivityDataSchema>;
export type BalanceSheetData = z.infer<typeof BalanceSheetDataSchema>;
export type CashFlowItem = z.infer<typeof CashFlowItemSchema>;
export type CashFlowData = z.infer<typeof CashFlowDataSchema>;
export type IndexReturn = z.infer<typeof IndexReturnSchema>;
export type MarketData = z.infer<typeof MarketDataSchema>;
export type StockDetail = z.infer<typeof StockDetailSchema>;
export type HoldingsDetailData = z.infer<typeof HoldingsDetailDataSchema>;
export type AnnualCategoryTotal = z.infer<typeof AnnualCategoryTotalSchema>;
export type AnnualSummary = z.infer<typeof AnnualSummarySchema>;
export type SnapshotPoint = z.infer<typeof SnapshotPointSchema>;
export type MonthlyFlowPoint = z.infer<typeof MonthlyFlowPointSchema>;
export type ChartData = z.infer<typeof ChartDataSchema>;
export type ReconciliationMatch = z.infer<typeof ReconciliationMatchSchema>;
export type CrossReconciliationData = z.infer<typeof CrossReconciliationDataSchema>;
export type TierReconciliation = z.infer<typeof TierReconciliationSchema>;
export type ReconciliationData = z.infer<typeof ReconciliationDataSchema>;
export type ReportData = z.infer<typeof ReportDataSchema>;
