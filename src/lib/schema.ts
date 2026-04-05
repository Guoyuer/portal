// ── Zod schemas for runtime validation of ReportData from R2 ─────────────
// Mirrors the interfaces in types.ts. If the Python pipeline changes its
// output shape, validation will catch the mismatch immediately instead of
// causing silent rendering bugs.

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

const AccountBalanceSchema = z.object({
  name: z.string(),
  balance: z.number(),
  currency: z.string(),
});

const BalanceSheetDataSchema = z.object({
  investmentTotal: z.number(),
  accounts: z.array(AccountBalanceSchema),
  accountsTotal: z.number(),
  creditCards: z.array(AccountBalanceSchema),
  totalLiabilities: z.number(),
  totalAssets: z.number(),
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
});

const MarketDataSchema = z.object({
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
  topPerformers: z.array(StockDetailSchema),
  bottomPerformers: z.array(StockDetailSchema),
  upcomingEarnings: z.array(StockDetailSchema),
  allStocks: z.array(StockDetailSchema).default([]),
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

/** Inferred type from the Zod schema — use this instead of the hand-written ReportData interface. */
export type ReportDataFromSchema = z.infer<typeof ReportDataSchema>;
