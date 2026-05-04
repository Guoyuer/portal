"use client";

import { useEffect, useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/hooks/use-bundle";
import { catColorByName, cashflowState, computeGroupedActivity, type CashflowState } from "@/lib/compute/compute";
import type { MonthlyFlowPoint } from "@/lib/compute/computed-types";
import { fmtDateMedium } from "@/lib/format/format";
import { SectionHeader, SectionBody, SectionMessage } from "@/components/finance/section";
import { TickerTable } from "@/components/finance/ticker-table";
import { EQUIVALENT_GROUPS } from "@/lib/data/equivalent-groups";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CashFlow } from "@/components/finance/cash-flow";
import { MarketContext } from "@/components/finance/market-context";
import { BackToTop } from "@/components/layout/back-to-top";
import { TimemachineSection, StickyBrush } from "@/components/finance/timemachine";
import { FinanceSkeleton } from "@/components/loading-skeleton";
import { ErrorBoundary, SectionError } from "@/components/error-boundary";
import { UnmatchedPanel } from "@/components/finance/unmatched-panel";

// ── Helpers ──────────────────────────────────────────────────────────

const PAGE_LOAD_TIME = Date.now();

function SyncStatus({ syncMeta }: { syncMeta: ReturnType<typeof useBundle>["syncMeta"] }) {
  const lastSync = syncMeta?.last_sync;
  if (!lastSync) return null;
  const daysAgo = Math.floor((PAGE_LOAD_TIME - new Date(lastSync).getTime()) / 86_400_000);
  if (daysAgo <= 3) return null;
  return <p className="text-xs mt-0.5 text-yellow-500">Stale — last sync {daysAgo}d ago</p>;
}

function CashFlowContent({
  state,
  monthlyFlows,
  activeMonth,
}: {
  state: CashflowState;
  monthlyFlows: MonthlyFlowPoint[];
  activeMonth: string | undefined;
}) {
  switch (state.kind) {
    case "unavailable":
      return <SectionMessage kind="unavailable">Cash flow data unavailable</SectionMessage>;
    case "empty":
      return <SectionMessage kind="empty">No transactions in this period</SectionMessage>;
    case "data":
      return (
        <>
          <SectionBody><CashFlow data={state.data} /></SectionBody>
          {monthlyFlows.length > 0 && (
            <div className="liquid-glass mt-4 overflow-hidden">
              <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                <IncomeExpensesChart data={monthlyFlows} activeMonth={activeMonth} />
              </div>
            </div>
          )}
        </>
      );
  }
}

function ActivityContent({
  activity,
  startDate,
  snapshotDate,
  dailyTickers,
  investmentTxns,
}: {
  activity: ReturnType<typeof useBundle>["activity"];
  startDate: string | null;
  snapshotDate: string | null;
  dailyTickers: ReturnType<typeof useBundle>["dailyTickers"];
  investmentTxns: ReturnType<typeof useBundle>["investmentTxns"];
}) {
  const [grouped, setGrouped] = useState(true);

  if (!activity) return <SectionMessage kind="unavailable">Activity data unavailable</SectionMessage>;
  const source = grouped && startDate && snapshotDate
    ? computeGroupedActivity(investmentTxns, startDate, snapshotDate)
    : activity;
  const { buysBySymbol, sellsBySymbol, dividendsBySymbol } = source;
  if (buysBySymbol.length === 0 && sellsBySymbol.length === 0 && dividendsBySymbol.length === 0) {
    return <SectionMessage kind="empty">No activity in this period</SectionMessage>;
  }
  return (
    <SectionBody>
      <div className="flex justify-end mb-2">
        <label
          className="inline-flex items-center gap-2 text-sm text-muted-foreground cursor-pointer"
          title={Object.values(EQUIVALENT_GROUPS)
            .map((g) => `${g.display}: ${g.tickers.join(", ")}`)
            .join("\n")}
        >
          <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
          Group equivalent tickers
        </label>
      </div>
      <div className="grid md:grid-cols-2 gap-6">
        <TickerTable title="Buys by Symbol" data={buysBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} dailyTickers={dailyTickers} investmentTxns={investmentTxns} />
        <TickerTable title="Sells by Symbol" data={sellsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} dailyTickers={dailyTickers} investmentTxns={investmentTxns} />
        <TickerTable title="Dividends by Symbol" data={dividendsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} countLabel="Payments" dailyTickers={dailyTickers} investmentTxns={investmentTxns} />
      </div>
    </SectionBody>
  );
}

// ── Finance Page ──────────────────────────────────────────────────────

export default function FinancePage() {
  const [allocOpen, setAllocOpen] = useState(false);
  const [unmatchedExpanded, setUnmatchedExpanded] = useState(false);
  const tl = useBundle();

  const { snapshotDate, startDate } = tl;

  // Tab title with date range
  useEffect(() => {
    if (startDate && snapshotDate) {
      const fmt = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(2, 4)}`;
      document.title = `Dashboard · ${fmt(startDate)}-${fmt(snapshotDate)}`;
    } else {
      document.title = "Dashboard";
    }
  }, [startDate, snapshotDate]);

  if (tl.loading) return <FinanceSkeleton />;
  if (tl.error) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-2">Failed to load data</p>
        <p className="text-sm text-muted-foreground">{tl.error}</p>
      </div>
    );
  }

  const {
    allocation, cashflow, activity, market, crossCheck,
    categories, chartDaily, monthlyFlows,
    syncMeta,
    brushStart, brushEnd, defaultStartIndex, defaultEndIndex, onBrushChange,
    dailyTickers, investmentTxns,
  } = tl;
  const colorByName = catColorByName(categories);
  const cfState = cashflowState(cashflow);

  return (
    <div data-testid="finance-page" className="max-w-5xl mx-auto space-y-10 pb-16">
      {/* Header */}
      <div>
        <h1 data-testid="page-title" className="text-xl sm:text-2xl font-semibold tracking-tight">
          Dashboard for Yuer
        </h1>
        {startDate && snapshotDate && (
          <p className="text-sm text-muted-foreground mt-1">
            {fmtDateMedium(startDate)}
            {" \u2014 "}
            {fmtDateMedium(snapshotDate)}
          </p>
        )}
        <SyncStatus syncMeta={syncMeta} />
      </div>

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Allocation" />}>
        {allocation ? (
          <MetricCards
            allocation={allocation}
            savingsRate={cashflow?.savingsRate ?? null}
            takehomeSavingsRate={cashflow?.takehomeSavingsRate ?? null}
            goal={GOAL}
            allocationOpen={allocOpen}
            onAllocationToggle={() => setAllocOpen((v) => !v)}
            colorByName={colorByName}
          />
        ) : (
          <SectionMessage kind="unavailable">Allocation data unavailable</SectionMessage>
        )}
      </ErrorBoundary>

      {/* ── 2. Timemachine ─────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Timemachine" />}>
        <TimemachineSection timeline={tl} />
      </ErrorBoundary>

      {/* ── 3. Portfolio Activity ───────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Investment Activity" />}>
        <section id="investment-activity" className="scroll-mt-20 md:scroll-mt-8">
          <SectionHeader>
            Investment Activity
            {crossCheck && (
              <button
                type="button"
                onClick={() => { if (!crossCheck.ok) setUnmatchedExpanded(v => !v); }}
                disabled={crossCheck.ok}
                className={`ml-2 inline-flex items-center gap-1 text-xs font-normal ${crossCheck.ok ? "text-green-500 cursor-default" : "text-red-400 cursor-pointer hover:text-red-300"}`}
                title={[
                  `Fidelity:   ${crossCheck.perSource.fidelity.matched}/${crossCheck.perSource.fidelity.total}`,
                  `Robinhood:  ${crossCheck.perSource.robinhood.matched}/${crossCheck.perSource.robinhood.total}`,
                ].join("\n")}
              >
                {crossCheck.ok ? "\u2713" : "\u2717"}{" "}
                {crossCheck.matchedCount}/{crossCheck.totalCount} deposits reconciled
              </button>
            )}
          </SectionHeader>
          {crossCheck && !crossCheck.ok && unmatchedExpanded && (
            <UnmatchedPanel items={crossCheck.allUnmatched} />
          )}
          <ActivityContent activity={activity} startDate={startDate} snapshotDate={snapshotDate} dailyTickers={dailyTickers} investmentTxns={investmentTxns} />
        </section>
      </ErrorBoundary>

      {/* ── 4. Cash Flow ────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Cash Flow" />}>
        <section id="cashflow" className="scroll-mt-20 md:scroll-mt-8">
          <SectionHeader>Cash Flow</SectionHeader>
          <CashFlowContent
            state={cfState}
            monthlyFlows={monthlyFlows}
            activeMonth={snapshotDate?.slice(0, 7)}
          />
        </section>
      </ErrorBoundary>

      {/* ── Market Context ──────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Market" />}>
        <div id="market" data-testid="market-section" className="scroll-mt-20 md:scroll-mt-8">
          {market ? (
            <MarketContext data={market} title="Market" />
          ) : (
            <>
              <SectionHeader>Market</SectionHeader>
              <SectionMessage kind="unavailable" wrap={false} data-testid="market-error">
                Market data unavailable
              </SectionMessage>
            </>
          )}
        </div>
      </ErrorBoundary>

      <BackToTop />

      {chartDaily.length > 0 && (
        <StickyBrush
          daily={chartDaily}
          defaultStartIndex={defaultStartIndex}
          defaultEndIndex={defaultEndIndex}
          brushStart={brushStart}
          brushEnd={brushEnd}
          onBrushChange={onBrushChange}
        />
      )}
    </div>
  );
}
