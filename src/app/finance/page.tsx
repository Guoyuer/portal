"use client";

import { useMemo, useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/use-bundle";
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

/** "2026-03-15" -> "2026-03" */
function dateToMonthKey(dateStr: string): string {
  return dateStr.slice(0, 7);
}

// ── Compute monthly flows from timeline prefix sums ───────────────────

function computeMonthlyFlows(prefix: PrefixPoint[], start: string | null, end: string | null): MonthlyFlowPoint[] {
  if (prefix.length === 0 || !start || !end) return [];

  // Filter prefix to brush range
  const filtered = prefix.filter((p) => p.date >= start && p.date <= end);
  if (filtered.length === 0) return [];

  // Group by month, take last entry per month as cumulative value
  const monthEnds = new Map<string, PrefixPoint>();
  for (const p of filtered) {
    monthEnds.set(p.date.slice(0, 7), p);
  }

  // We also need the prefix point just before the range to compute the first month's delta
  const beforeRange = prefix.filter((p) => p.date < start);
  const baseline = beforeRange.length > 0 ? beforeRange[beforeRange.length - 1] : null;

  const months = Array.from(monthEnds.entries()).sort(([a], [b]) => a.localeCompare(b));
  const result: MonthlyFlowPoint[] = [];

  for (let i = 0; i < months.length; i++) {
    const [month, curr] = months[i];
    // For the first month, use the point before the range as baseline
    const prev = i > 0 ? months[i - 1][1] : baseline;

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

  // ── Bundle (single fetch, local computation) ──────────────────────
  const tl = useBundle();

  // ── Derived dates from timeline ───────────────────────────────────
  const snapshotDate = tl.snapshot?.date ?? null;
  const startDate = tl.startDate;

  // ── Derived values ────────────────────────────────────────────────
  const goalPct = tl.allocation ? (tl.allocation.total / GOAL) * 100 : 0;
  const period = snapshotDate ? dateToPeriod(snapshotDate) : "";
  const invested = tl.range?.buys ?? 0;
  const alloc = tl.allocation;
  const cf = tl.cashflow;
  const act = tl.activity;
  const mkt = tl.market;

  const monthlyFlows = useMemo(
    () => computeMonthlyFlows(tl.prefix, startDate, snapshotDate),
    [tl.prefix, startDate, snapshotDate],
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

      {/* ── 2. Timemachine ─────────────────────────────────────────────── */}
      <TimemachineSection timeline={tl} fallback={<NetWorthGrowth data={[]} />} />

      {/* ── 4. Cash Flow ────────────────────────────────────────────────── */}
      <section id="cashflow">
        <SectionHeader>{SECTION_LABELS["cashflow"]}{cf ? ` \u2014 ${period}` : ""}</SectionHeader>
        {cf ? (
          <>
            <SectionBody>
              <CashFlow data={cf} />
            </SectionBody>

            {/* Stat bar + chart -- single glass container, no internal borders */}
            <div className="liquid-glass mt-4 overflow-hidden">
              <CashFlowStatBar data={cf} invested={invested} period={period} />
              <div className="mx-3 sm:mx-5 h-px bg-gradient-to-r from-transparent via-foreground/8 to-transparent" />
              {monthlyFlows.length > 0 ? (
                <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                  <IncomeExpensesChart
                    data={monthlyFlows}
                    activeMonth={snapshotDate ? dateToMonthKey(snapshotDate) : undefined}
                  />
                </div>
              ) : (
                <p className="text-sm text-red-400 px-4 pb-4">Monthly flow data unavailable</p>
              )}
            </div>
          </>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Cash flow data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── 5. Portfolio Activity ───────────────────────────────────────── */}
      <section id="fidelity-activity">
        <SectionHeader>
          {SECTION_LABELS["fidelity-activity"]}
          {tl.reconcile && (
            <span
              className={`ml-2 inline-flex items-center gap-1 text-xs font-normal ${tl.reconcile.ok ? "text-green-500" : "text-red-400"}`}
              title={tl.reconcile.ok
                ? `${tl.reconcile.matched}/${tl.reconcile.total} deposits matched with Qianji`
                : `${tl.reconcile.unmatchedFidelity} of ${tl.reconcile.total} deposits not found in Qianji`}
            >
              {tl.reconcile.ok ? "\u2713" : "\u2717"}
            </span>
          )}
        </SectionHeader>
        {act ? (
          <SectionBody>
            <PortfolioActivity activity={act} periodLabel={activityPeriodLabel} />
          </SectionBody>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Activity data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── Market Context ──────────────────────────────────────────────── */}
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

      <BackToTop />
    </div>
  );
}
