"use client";

import { useMemo, useState } from "react";
import { GOAL } from "@/lib/config";
import { useBundle } from "@/lib/use-bundle";
import type { MonthlyFlowPoint, QianjiTxn } from "@/lib/schema";
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

/** "2026-03-15" -> "2026-03" */
function dateToMonthKey(dateStr: string): string {
  return dateStr.slice(0, 7);
}

// ── Compute monthly flows from raw Qianji transactions ────────────────

function computeMonthlyFlows(qianjiTxns: QianjiTxn[], start: string | null, end: string | null): MonthlyFlowPoint[] {
  if (!qianjiTxns.length || !start || !end) return [];

  const months = new Map<string, { income: number; expenses: number }>();

  for (const t of qianjiTxns) {
    if (t.date < start || t.date > end) continue;
    const month = t.date.slice(0, 7);
    const entry = months.get(month) ?? { income: 0, expenses: 0 };
    if (t.type === "income") entry.income += t.amount;
    else if (t.type === "expense") entry.expenses += t.amount;
    months.set(month, entry);
  }

  return Array.from(months.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([month, { income, expenses }]) => ({
      month,
      income: Math.round(income * 100) / 100,
      expenses: Math.round(expenses * 100) / 100,
      savingsRate: income > 0 ? Math.round(((income - expenses) / income) * 10000) / 100 : 0,
    }));
}

const PAGE_LOAD_TIME = Date.now();

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

  const monthlyFlows = useMemo(
    () => computeMonthlyFlows(tl.qianjiTxns, startDate, snapshotDate),
    [tl.qianjiTxns, startDate, snapshotDate],
  );

  const syncStale = useMemo(() => {
    const lastSync = tl.syncMeta?.last_sync;
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
  }, [tl.syncMeta]);

  // ── Loading state ─────────────────────────────────────────────────
  if (tl.loading) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center text-muted-foreground">
        Loading...
      </div>
    );
  }

  if (tl.error) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-2">Failed to load data</p>
        <p className="text-sm text-muted-foreground">{tl.error}</p>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <div>
        <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">
          Dashboard for Yuer
        </h1>
        {startDate && snapshotDate && (
          <p className="text-sm text-muted-foreground mt-1">
            {new Date(startDate).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
            {" \u2014 "}
            {new Date(snapshotDate).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
          </p>
        )}
        {syncStale}
      </div>

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

      {/* ── 3. Cash Flow ────────────────────────────────────────────────── */}
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
                      activeMonth={snapshotDate ? dateToMonthKey(snapshotDate) : undefined}
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

      {/* ── 4. Portfolio Activity ───────────────────────────────────────── */}
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
