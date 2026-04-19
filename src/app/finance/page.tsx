"use client";

import { useEffect, useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/hooks/use-bundle";
import { catColorByName, cashflowState, type CashflowState, type GroupedActivityResponse } from "@/lib/compute/compute";
import type { MonthlyFlowPoint } from "@/lib/compute/computed-types";
import { fmtDateMedium } from "@/lib/format/format";
import { SectionHeader, SectionBody, SectionMessage } from "@/components/finance/section";
import { TickerTable } from "@/components/finance/ticker-table";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CashFlow } from "@/components/finance/cash-flow";
import { MarketContext } from "@/components/finance/market-context";
import { BackToTop } from "@/components/layout/back-to-top";
import { TimemachineSection, StickyBrush } from "@/components/finance/timemachine";
import { FinanceSkeleton } from "@/components/loading-skeleton";
import { ErrorBoundary, SectionError } from "@/components/error-boundary";

// ── Helpers ──────────────────────────────────────────────────────────

const PAGE_LOAD_TIME = Date.now();

function SyncStatus({ syncMeta }: { syncMeta: Record<string, string> | null }) {
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
  groupedActivity,
  startDate,
  snapshotDate,
}: {
  activity: ReturnType<typeof useBundle>["activity"];
  groupedActivity: GroupedActivityResponse | null;
  startDate: string | null;
  snapshotDate: string | null;
}) {
  const [grouped, setGrouped] = useState(true);

  if (!activity) return <SectionMessage kind="unavailable">Activity data unavailable</SectionMessage>;
  const source = grouped && groupedActivity ? groupedActivity : activity;
  const { buysBySymbol, sellsBySymbol, dividendsBySymbol } = source;
  if (buysBySymbol.length === 0 && sellsBySymbol.length === 0 && dividendsBySymbol.length === 0) {
    return <SectionMessage kind="empty">No activity in this period</SectionMessage>;
  }
  return (
    <SectionBody>
      <div className="flex justify-end mb-2">
        <label className="inline-flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={grouped} onChange={(e) => setGrouped(e.target.checked)} />
          Group equivalent tickers
        </label>
      </div>
      <div className="grid md:grid-cols-2 gap-6">
        <TickerTable title="Buys by Symbol" data={buysBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
        <TickerTable title="Sells by Symbol" data={sellsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
        <TickerTable title="Dividends by Symbol" data={dividendsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} countLabel="Payments" />
      </div>
    </SectionBody>
  );
}

// ── Finance Page ──────────────────────────────────────────────────────

export default function FinancePage() {
  const [allocOpen, setAllocOpen] = useState(false);
  const tl = useBundle();

  const snapshotDate = tl.snapshot?.date ?? null;
  const startDate = tl.startDate;

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
    allocation, cashflow, activity, groupedActivity, market, crossCheck,
    categories, chartDaily, monthlyFlows,
    syncMeta, marketError,
    brushStart, brushEnd, defaultStartIndex, defaultEndIndex, onBrushChange,
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
      <ErrorBoundary fallback={<SectionError label="Fidelity Activity" />}>
        <section id="fidelity-activity" className="scroll-mt-20 md:scroll-mt-8">
          <SectionHeader>
            Fidelity Activity
            {crossCheck && (
              <span
                className={`ml-2 inline-flex items-center gap-1 text-xs font-normal ${crossCheck.ok ? "text-green-500" : "text-red-400"}`}
                title={crossCheck.ok
                  ? "All Fidelity deposits matched with Qianji transfers"
                  : `${crossCheck.totalCount - crossCheck.matchedCount} of ${crossCheck.totalCount} deposits not found in Qianji`}
              >
                {crossCheck.ok ? "\u2713" : "\u2717"}{" "}
                {crossCheck.matchedCount}/{crossCheck.totalCount} deposits reconciled
              </span>
            )}
          </SectionHeader>
          <ActivityContent activity={activity} groupedActivity={groupedActivity} startDate={startDate} snapshotDate={snapshotDate} />
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
                Market data failed to load{marketError ? `: ${marketError}` : ""}
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
