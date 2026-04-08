"use client";

import { useMemo, useState } from "react";
import { GOAL } from "@/lib/config";
import { useTimeline } from "@/lib/use-timeline";
import { useAllocation, useCashflow, useActivity, useMarket } from "@/lib/use-api";
import { adaptCashflow } from "@/components/finance/cash-flow";
import type { MonthlyFlowPoint, PrefixPoint } from "@/lib/schema";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CashFlow, CashFlowStatBar } from "@/components/finance/cash-flow";
import { PortfolioActivity } from "@/components/finance/portfolio-activity";
import { MarketContext } from "@/components/finance/market-context";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { BackToTop } from "@/components/layout/back-to-top";
import { TimemachineSection } from "@/components/finance/timemachine";

// ── Helpers ──────────────────────────────────────────────────────────

/** "2026-03-15" -> "March 2026" */
function dateToPeriod(dateStr: string): string {
  const [y, m] = dateStr.split("-");
  const dt = new Date(+y, +m - 1, 1);
  return dt.toLocaleDateString("en-US", { month: "long", year: "numeric" });
}

/** "March 2026" -> "2026-03" */
function periodToMonthKey(period: string): string | undefined {
  const MONTH_MAP: Record<string, string> = {
    January: "01", February: "02", March: "03", April: "04",
    May: "05", June: "06", July: "07", August: "08",
    September: "09", October: "10", November: "11", December: "12",
  };
  const [name, year] = period.split(" ");
  const mm = MONTH_MAP[name];
  return mm && year ? `${year}-${mm}` : undefined;
}

// ── Compute monthly flows from timeline prefix sums ───────────────────

function computeMonthlyFlows(prefix: PrefixPoint[]): MonthlyFlowPoint[] {
  if (prefix.length === 0) return [];

  // Group by month, take last entry per month as cumulative value
  const monthEnds = new Map<string, PrefixPoint>();
  for (const p of prefix) {
    const monthKey = p.date.slice(0, 7); // "YYYY-MM"
    monthEnds.set(monthKey, p);
  }

  const months = Array.from(monthEnds.entries()).sort(([a], [b]) => a.localeCompare(b));
  const result: MonthlyFlowPoint[] = [];

  for (let i = 0; i < months.length; i++) {
    const [month, curr] = months[i];
    const prev = i > 0 ? months[i - 1][1] : null;

    const income = curr.income - (prev?.income ?? 0);
    const expenses = curr.expenses - (prev?.expenses ?? 0);
    const savingsRate = income > 0 ? ((income - expenses) / income) * 100 : 0;

    result.push({ month, income, expenses, savingsRate });
  }

  return result;
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

  // ── Timeline (drives brush + date selection) ──────────────────────
  const tl = useTimeline();

  // ── Derived dates from timeline ───────────────────────────────────
  const snapshotDate = tl.snapshot?.date ?? null;
  const startDate = tl.startDate;

  // ── API hooks ─────────────────────────────────────────────────────
  const alloc = useAllocation(snapshotDate);
  const cf = useCashflow(startDate, snapshotDate);
  const act = useActivity(startDate, snapshotDate);
  const mkt = useMarket();

  // ── Derived values ────────────────────────────────────────────────
  const goalPct = alloc.data ? (alloc.data.total / GOAL) * 100 : 0;

  const period = snapshotDate ? dateToPeriod(snapshotDate) : "";

  const cashflowData = useMemo(() => {
    if (!cf.data) return null;
    const invested = tl.range?.buys ?? 0;
    return adaptCashflow(cf.data, period, invested);
  }, [cf.data, period, tl.range?.buys]);

  const monthlyFlows = useMemo(
    () => computeMonthlyFlows(tl.prefix),
    [tl.prefix],
  );

  // ── Loading state ─────────────────────────────────────────────────
  if (tl.loading) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center text-muted-foreground">
        Loading...
      </div>
    );
  }

  // ── Period label for activity ──────────────────────────────────────
  const activityPeriodLabel = startDate && snapshotDate
    ? `${startDate} \u2013 ${snapshotDate}`
    : undefined;

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">
        Dashboard for Yuer
      </h1>

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      {alloc.data ? (
        <MetricCards
          total={alloc.data.total}
          netWorth={alloc.data.netWorth}
          categories={alloc.data.categories}
          tickers={alloc.data.tickers}
          savingsRate={cf.data?.savingsRate ?? null}
          takehomeSavingsRate={cf.data?.savingsRate ?? null}
          goal={GOAL}
          goalPct={goalPct}
          allocationOpen={allocOpen}
          onAllocationToggle={() => setAllocOpen((v) => !v)}
        />
      ) : alloc.loading ? (
        <div className="liquid-glass p-4 text-center text-muted-foreground">Loading allocation...</div>
      ) : (
        <div className="liquid-glass p-4 text-center text-sm text-red-400">Allocation data unavailable</div>
      )}

      {/* ── 2. Timemachine ─────────────────────────────────────────────── */}
      <TimemachineSection timeline={tl} fallback={<NetWorthGrowth data={[]} />} />

      {/* ── 4. Cash Flow ────────────────────────────────────────────────── */}
      <section id="cashflow">
        <SectionHeader>{SECTION_LABELS["cashflow"]}{cashflowData ? ` \u2014 ${cashflowData.period}` : ""}</SectionHeader>
        {cashflowData ? (
          <>
            <SectionBody>
              <CashFlow data={cashflowData} />
            </SectionBody>

            {/* Stat bar + chart — single glass container, no internal borders */}
            <div className="liquid-glass mt-4 overflow-hidden">
              <CashFlowStatBar data={cashflowData} period={cashflowData.period} />
              <div className="mx-3 sm:mx-5 h-px bg-gradient-to-r from-transparent via-foreground/8 to-transparent" />
              {monthlyFlows.length > 0 ? (
                <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                  <IncomeExpensesChart
                    data={monthlyFlows}
                    activeMonth={periodToMonthKey(cashflowData.period)}
                  />
                </div>
              ) : (
                <p className="text-sm text-red-400 px-4 pb-4">Monthly flow data unavailable</p>
              )}
            </div>
          </>
        ) : cf.loading ? (
          <SectionBody><p className="text-sm text-muted-foreground">Loading cash flow...</p></SectionBody>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Cash flow data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── 5. Portfolio Activity ───────────────────────────────────────── */}
      <section id="fidelity-activity">
        <SectionHeader>{SECTION_LABELS["fidelity-activity"]}</SectionHeader>
        {act.data ? (
          <SectionBody>
            <PortfolioActivity activity={act.data} periodLabel={activityPeriodLabel} />
          </SectionBody>
        ) : act.loading ? (
          <SectionBody><p className="text-sm text-muted-foreground">Loading activity...</p></SectionBody>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Activity data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── Market Context ──────────────────────────────────────────────── */}
      <div id="market">
        {mkt.data ? (
          <MarketContext data={mkt.data} title={SECTION_LABELS["market"]} />
        ) : mkt.loading ? (
          <>
            <SectionHeader>{SECTION_LABELS["market"]}</SectionHeader>
            <p className="text-sm text-muted-foreground">Loading market data...</p>
          </>
        ) : (
          <>
            <SectionHeader>{SECTION_LABELS["market"]}</SectionHeader>
            <p className="text-sm text-red-400">Market data unavailable</p>
          </>
        )}
      </div>

      <BackToTop />
    </div>
  );
}
