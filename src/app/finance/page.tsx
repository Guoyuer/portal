"use client";

import { useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/use-bundle";
import { computeMonthlyFlows } from "@/lib/compute";
import { fmtDateMedium } from "@/lib/format";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CashFlow, CashFlowStatBar } from "@/components/finance/cash-flow";
import { PortfolioActivity } from "@/components/finance/portfolio-activity";
import { SavingsTrend } from "@/components/finance/savings-trend";
import { MarketContext } from "@/components/finance/market-context";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { BackToTop } from "@/components/layout/back-to-top";
import { TimemachineSection, StickyBrush } from "@/components/finance/timemachine";
import { FinanceSkeleton } from "@/components/loading-skeleton";
import { ErrorBoundary, SectionError } from "@/components/error-boundary";

// ── Helpers ──────────────────────────────────────────────────────────

const PAGE_LOAD_TIME = Date.now();

function SyncStatus({ syncMeta }: { syncMeta: Record<string, string> | null }) {
  const lastSync = syncMeta?.last_sync;
  if (!lastSync) return null;
  const syncDate = new Date(lastSync);
  const daysAgo = Math.floor((PAGE_LOAD_TIME - syncDate.getTime()) / 86_400_000);
  const stale = daysAgo > 3;
  return (
    <p className={`text-xs mt-0.5 ${stale ? "text-yellow-500" : "text-muted-foreground/60"}`}>
      Data as of {syncDate.toLocaleDateString("en-US", { month: "short", day: "numeric" })}
      {stale && ` (${daysAgo}d ago)`}
    </p>
  );
}

// ── Sections ─────────────────────────────────────────────────────────

const SECTION_LABELS = {
  "cashflow": "Cash Flow",
  "fidelity-activity": "Fidelity Activity",
  "market": "Market",
} as const;

// ── Finance Page ──────────────────────────────────────────────────────

export default function FinancePage() {
  const [allocOpen, setAllocOpen] = useState(false);

  // ── Bundle (single fetch, local computation) ──────────────────────
  const tl = useBundle();

  // ── Derived dates from timeline ───────────────────────────────────
  const snapshotDate = tl.snapshot?.date ?? null;
  const startDate = tl.startDate;

  // ── Derived values ────────────────────────────────────────────────
  const goalPct = tl.allocation ? (tl.allocation.total / GOAL) * 100 : 0;
  const invested = tl.activity?.buysBySymbol.reduce((s, b) => s + b.total, 0) ?? 0;
  const alloc = tl.allocation;
  const cf = tl.cashflow;
  const act = tl.activity;
  const mkt = tl.market;

  const monthlyFlows = computeMonthlyFlows(tl.qianjiTxns, startDate, snapshotDate);
  const allMonthlyFlows = computeMonthlyFlows(tl.qianjiTxns, tl.chartDaily[0]?.date ?? null, tl.chartDaily[tl.chartDaily.length - 1]?.date ?? null);

  // ── Loading state ─────────────────────────────────────────────────
  if (tl.loading) return <FinanceSkeleton />;

  if (tl.error) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-2">Failed to load data</p>
        <p className="text-sm text-muted-foreground">{tl.error}</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-10 pb-16">
      {/* Header */}
      <div>
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">
          Dashboard for Yuer
        </h1>
        {startDate && snapshotDate && (
          <p className="text-sm text-muted-foreground mt-1">
            {fmtDateMedium(startDate)}
            {" \u2014 "}
            {fmtDateMedium(snapshotDate)}
          </p>
        )}
        <SyncStatus syncMeta={tl.syncMeta} />
      </div>

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Allocation" />}>
        {alloc ? (
          <MetricCards
            total={alloc.total}
            netWorth={alloc.netWorth}
            categories={alloc.categories}
            tickers={alloc.tickers}
            savingsRate={cf?.savingsRate ?? null}
            takehomeSavingsRate={cf?.takehomeSavingsRate ?? null}
            goal={GOAL}
            goalPct={goalPct}
            allocationOpen={allocOpen}
            onAllocationToggle={() => setAllocOpen((v) => !v)}
          />
        ) : (
          <div className="liquid-glass p-4 text-center text-sm text-red-400">Allocation data unavailable</div>
        )}
      </ErrorBoundary>

      {/* ── 2. Timemachine ─────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Timemachine" />}>
        <TimemachineSection timeline={tl} fallback={<NetWorthGrowth data={[]} />} />
      </ErrorBoundary>

      {/* ── 3. Cash Flow ────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Cash Flow" />}>
        <section id="cashflow">
          <SectionHeader>{SECTION_LABELS["cashflow"]}</SectionHeader>
          {cf ? (
            cf.totalIncome === 0 && cf.totalExpenses === 0 ? (
              <SectionBody><p className="text-sm text-muted-foreground">No transactions in this period</p></SectionBody>
            ) : (
              <>
                <SectionBody>
                  <CashFlow data={cf} />
                </SectionBody>

                {/* Stat bar + chart -- single glass container, no internal borders */}
                <div className="liquid-glass mt-4 overflow-hidden">
                  <CashFlowStatBar data={cf} invested={invested} />
                  <div className="mx-3 sm:mx-5 h-px bg-gradient-to-r from-transparent via-foreground/8 to-transparent" />
                  {monthlyFlows.length > 0 ? (
                    <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                      <IncomeExpensesChart
                        data={monthlyFlows}
                        activeMonth={snapshotDate?.slice(0, 7)}
                      />
                    </div>
                  ) : (
                    <p className="text-sm text-red-400 px-4 pb-4">Monthly flow data unavailable</p>
                  )}
                </div>
              </>
            )
          ) : (
            <SectionBody><p className="text-sm text-red-400">Cash flow data unavailable</p></SectionBody>
          )}
        </section>
      </ErrorBoundary>

      {/* ── 3.5 Savings Rate Trend ─────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Savings Trend" />}>
        <section id="savings-trend">
          <SectionHeader>Savings Rate Trend</SectionHeader>
          {allMonthlyFlows.length > 0 ? (
            <SectionBody>
              <SavingsTrend data={allMonthlyFlows} />
            </SectionBody>
          ) : (
            <SectionBody><p className="text-sm text-muted-foreground">No data available</p></SectionBody>
          )}
        </section>
      </ErrorBoundary>

      {/* ── 4. Portfolio Activity ───────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Fidelity Activity" />}>
        <section id="fidelity-activity">
          <SectionHeader>
            {SECTION_LABELS["fidelity-activity"]}
            {tl.crossCheck && (
              <span
                className={`ml-2 inline-flex items-center gap-1 text-xs font-normal ${tl.crossCheck.ok ? "text-green-500" : "text-red-400"}`}
                title={tl.crossCheck.ok
                  ? `${tl.crossCheck.matchedCount}/${tl.crossCheck.totalCount} deposits matched with Qianji`
                  : `${tl.crossCheck.totalCount - tl.crossCheck.matchedCount} of ${tl.crossCheck.totalCount} deposits not found in Qianji`}
              >
                {tl.crossCheck.ok ? "\u2713" : "\u2717"}
              </span>
            )}
          </SectionHeader>
          {act ? (
            act.buysBySymbol.length === 0 && act.sellsBySymbol.length === 0 && act.dividendsBySymbol.length === 0 ? (
              <SectionBody><p className="text-sm text-muted-foreground">No activity in this period</p></SectionBody>
            ) : (
              <SectionBody>
                <PortfolioActivity activity={act} />
              </SectionBody>
            )
          ) : (
            <SectionBody><p className="text-sm text-red-400">Activity data unavailable</p></SectionBody>
          )}
        </section>
      </ErrorBoundary>

      {/* ── Market Context ──────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Market" />}>
        <div id="market">
          {mkt ? (
            <MarketContext data={mkt} title={SECTION_LABELS["market"]} />
          ) : (
            <>
              <SectionHeader>{SECTION_LABELS["market"]}</SectionHeader>
              <p className="text-sm text-red-400">Market data unavailable</p>
            </>
          )}
        </div>
      </ErrorBoundary>

      <BackToTop />

      {!tl.loading && !tl.error && tl.chartDaily.length > 0 && (
        <StickyBrush
          daily={tl.chartDaily}
          defaultStartIndex={tl.defaultStartIndex}
          defaultEndIndex={tl.defaultEndIndex}
          onBrushChange={tl.onBrushChange}
        />
      )}
    </div>
  );
}
