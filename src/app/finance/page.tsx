"use client";

import { useEffect, useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/use-bundle";
import { fmtDateMedium } from "@/lib/format";
import { SectionHeader, SectionBody, TickerTable } from "@/components/finance/shared";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CashFlow } from "@/components/finance/cash-flow";
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

  // ── Tab title with date range ─────────────────────────────────────
  useEffect(() => {
    if (startDate && snapshotDate) {
      const fmt = (iso: string) => `${iso.slice(5, 7)}/${iso.slice(8, 10)}/${iso.slice(2, 4)}`;
      document.title = `Dashboard · ${fmt(startDate)}-${fmt(snapshotDate)}`;
    } else {
      document.title = "Dashboard";
    }
  }, [startDate, snapshotDate]);

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
        <SyncStatus syncMeta={tl.syncMeta} />
      </div>

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Allocation" />}>
        {tl.allocation ? (
          <MetricCards
            allocation={tl.allocation}
            savingsRate={tl.cashflow?.savingsRate ?? null}
            takehomeSavingsRate={tl.cashflow?.takehomeSavingsRate ?? null}
            goal={GOAL}
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
          {tl.cashflow ? (
            tl.cashflow.totalIncome === 0 && tl.cashflow.totalExpenses === 0 ? (
              <SectionBody><p className="text-sm text-muted-foreground">No transactions in this period</p></SectionBody>
            ) : (
              <>
                <SectionBody>
                  <CashFlow data={tl.cashflow} />
                </SectionBody>

                {tl.monthlyFlows.length > 0 && (
                  <div className="liquid-glass mt-4 overflow-hidden">
                    <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                      <IncomeExpensesChart
                        data={tl.monthlyFlows}
                        activeMonth={snapshotDate?.slice(0, 7)}
                      />
                    </div>
                  </div>
                )}
              </>
            )
          ) : (
            <SectionBody><p className="text-sm text-red-400">Cash flow data unavailable</p></SectionBody>
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
          {tl.activity ? (
            tl.activity.buysBySymbol.length === 0 && tl.activity.sellsBySymbol.length === 0 && tl.activity.dividendsBySymbol.length === 0 ? (
              <SectionBody><p className="text-sm text-muted-foreground">No activity in this period</p></SectionBody>
            ) : (
              <SectionBody>
                <div className="grid md:grid-cols-2 gap-6">
                  <TickerTable title="Buys by Symbol" data={tl.activity.buysBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
                  <TickerTable title="Dividends by Symbol" data={tl.activity.dividendsBySymbol} startDate={startDate ?? undefined} endDate={snapshotDate ?? undefined} />
                </div>
              </SectionBody>
            )
          ) : (
            <SectionBody><p className="text-sm text-red-400">Activity data unavailable</p></SectionBody>
          )}
        </section>
      </ErrorBoundary>

      {/* ── Market Context ──────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Market" />}>
        <div id="market" data-testid="market-section">
          {tl.market ? (
            <MarketContext data={tl.market} title={SECTION_LABELS["market"]} />
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
          brushStart={tl.brushStart}
          brushEnd={tl.brushEnd}
          onBrushChange={tl.onBrushChange}
        />
      )}
    </div>
  );
}
