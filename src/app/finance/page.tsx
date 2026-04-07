"use client";

import { useCallback, useEffect, useState } from "react";
import { REPORT_URL } from "@/lib/config";
import { ReportDataSchema, type ReportData } from "@/lib/schema";
import { useActiveSection } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CategorySummary } from "@/components/finance/category-summary";
import { CashFlow, CashFlowStatBar } from "@/components/finance/cash-flow";
import { PortfolioActivity } from "@/components/finance/portfolio-activity";
import { MarketContext } from "@/components/finance/market-context";
import { AnnualSummary } from "@/components/finance/annual-summary";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { BackToTop } from "@/components/layout/back-to-top";

// ── Helpers ──────────────────────────────────────────────────────────

const MONTH_MAP: Record<string, string> = {
  January: "01", February: "02", March: "03", April: "04",
  May: "05", June: "06", July: "07", August: "08",
  September: "09", October: "10", November: "11", December: "12",
};

/** "March 2026" → "2026-03" */
function periodToMonthKey(period: string): string | undefined {
  const [name, year] = period.split(" ");
  const m = MONTH_MAP[name];
  return m && year ? `${year}-${m}` : undefined;
}

// ── Sections ─────────────────────────────────────────────────────────

const SECTION_LABELS = {
  "net-worth": "Net Worth",
  "allocation": "Allocation",
  "cashflow": "Cash Flow",
  "fidelity-activity": "Fidelity Activity",
  "market": "Market",
} as const;

type SectionId = keyof typeof SECTION_LABELS;

const SECTION_IDS = Object.keys(SECTION_LABELS) as SectionId[];

// ── Finance Page ──────────────────────────────────────────────────────

export default function FinancePage() {
  const [r, setReport] = useState<ReportData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const fetchReport = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(REPORT_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const json = await res.json();
      const parsed = ReportDataSchema.safeParse(json);
      if (!parsed.success) {
        console.error("Report validation failed:", parsed.error.issues);
        throw new Error(`Invalid report data: ${parsed.error.issues[0]?.message ?? "schema mismatch"}`);
      }
      setReport(parsed.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReport(); }, [fetchReport]);

  const { active, scrollTo } = useActiveSection(SECTION_IDS, !!r);

  if (loading) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center text-muted-foreground">
        Loading report...
      </div>
    );
  }

  if (error || !r) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-4">{error ?? "No data"}</p>
        <Button onClick={fetchReport} variant="outline">Retry</Button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">
        Portfolio Snapshot &mdash; {r.date}
      </h1>

      {/* Data timestamps — compact */}
      {r.metadata && (
        <p className="text-[11px] text-foreground/40 -mt-6 font-mono tracking-tight">
          Data: {r.metadata.positionsDate || "?"} · {r.metadata.historyDate || "?"} · {r.metadata.qianjiDate || "?"}
        </p>
      )}

      {/* Section nav */}
      <nav className="sticky top-0 z-30 -mx-6 pl-14 md:pl-6 pr-6 py-2 bg-background/80 backdrop-blur-xl backdrop-saturate-150 border-b border-white/20 dark:border-white/8 !rounded-none overflow-x-auto scrollbar-none flex gap-2 text-sm">
        {SECTION_IDS.map((id) => (
          <button
            key={id}
            onClick={() => scrollTo(id)}
            className={`whitespace-nowrap px-3 py-1 rounded-full transition-all ${
              active === id
                ? "bg-foreground/10 dark:bg-white/12 text-foreground font-medium"
                : "text-muted-foreground hover:text-foreground hover:bg-foreground/5"
            }`}
          >
            {SECTION_LABELS[id]}
          </button>
        ))}
      </nav>

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      <MetricCards report={r} />

      {/* ── 2. Net Worth ────────────────────────────────────────────────── */}
      <div id="net-worth">
        <NetWorthGrowth data={r.chartData?.netWorthTrend ?? []} />
      </div>

      {/* ── 3. Allocation ───────────────────────────────────────────────── */}
      <div id="allocation">
        <CategorySummary report={r} title={SECTION_LABELS["allocation"]} />
      </div>

      {/* ── 4. Cash Flow ────────────────────────────────────────────────── */}
      <section id="cashflow">
        <SectionHeader>{SECTION_LABELS["cashflow"]}{r.cashflow ? ` — ${r.cashflow.period}` : ""}</SectionHeader>
        {r.cashflow ? (
          <>
            <SectionBody>
              <CashFlow data={r.cashflow} />
            </SectionBody>

            {/* Stat bar + chart — single glass container, no internal borders */}
            <div className="liquid-glass mt-4 overflow-hidden">
              <CashFlowStatBar data={r.cashflow} period={r.cashflow.period} />
              <div className="mx-3 sm:mx-5 h-px bg-gradient-to-r from-transparent via-foreground/8 to-transparent" />
              {r.chartData?.monthlyFlows && r.chartData.monthlyFlows.length > 0 ? (
                <div className="px-3 sm:px-5 pb-3 sm:pb-5 pt-3">
                  <IncomeExpensesChart
                    data={r.chartData.monthlyFlows}
                    activeMonth={periodToMonthKey(r.cashflow.period)}
                  />
                </div>
              ) : (
                <p className="text-sm text-red-400 px-4 pb-4">Monthly flow data unavailable</p>
              )}
            </div>

            {r.annualSummary && (
              <details className="liquid-glass mt-4 p-3 sm:p-5">
                <summary className="font-semibold cursor-pointer hover:text-foreground">
                  {r.annualSummary.year} Year-to-Date
                </summary>
                <div className="mt-4"><AnnualSummary data={r.annualSummary} /></div>
              </details>
            )}
          </>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Cash flow data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── 5. Portfolio Activity ───────────────────────────────────────── */}
      <section id="fidelity-activity">
        <SectionHeader>{SECTION_LABELS["fidelity-activity"]}</SectionHeader>
        {r.activity ? (
          <SectionBody>
            <PortfolioActivity activity={r.activity} reconciliation={r.reconciliation} />
          </SectionBody>
        ) : (
          <SectionBody><p className="text-sm text-red-400">Activity data unavailable</p></SectionBody>
        )}
      </section>

      {/* ── Market Context ──────────────────────────────────────────────── */}
      <div id="market">
        {r.market ? (
          <MarketContext data={r.market} title={SECTION_LABELS["market"]} />
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
