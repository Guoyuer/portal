"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { REPORT_URL } from "@/lib/config";
import { ReportDataSchema, type ReportData } from "@/lib/schema";
import { fmtCurrency, fmtPct } from "@/lib/format";
import { useActiveSection } from "@/lib/hooks";
import { valueColor } from "@/lib/style-helpers";
import type { StockDetail } from "@/lib/types";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { IncomeExpensesChart } from "@/components/finance/charts";
import { MetricCards } from "@/components/finance/metric-cards";
import { CategorySummary } from "@/components/finance/category-summary";
import { CashFlow } from "@/components/finance/cash-flow";
import { PortfolioActivity } from "@/components/finance/portfolio-activity";
import { MarketContext } from "@/components/finance/market-context";
import { GainLoss } from "@/components/finance/gain-loss";
import { AnnualSummary } from "@/components/finance/annual-summary";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { BackToTop } from "@/components/layout/back-to-top";

// ── Performers Table ──────────────────────────────────────────────────

function PerformersTable({ title, data }: { title: string; data: StockDetail[] }) {
  if (data.length === 0) return null;
  return (
    <div className="mb-6 overflow-x-auto">
      <h3 className="font-semibold mb-2">{title}</h3>
      <Table>
        <TableHeader><TableRow>
          <TableHead>Ticker</TableHead>
          <TableHead className="text-right">Month Return</TableHead>
          <TableHead className="text-right">Value</TableHead>
          <TableHead className="text-right">vs 52W High</TableHead>
        </TableRow></TableHeader>
        <TableBody>
          {data.map((s) => (
            <TableRow key={s.ticker} className="even:bg-muted/50">
              <TableCell className="font-mono">{s.ticker}</TableCell>
              <TableCell className={`text-right ${valueColor(s.monthReturn)}`}>{fmtPct(s.monthReturn, true)}</TableCell>
              <TableCell className="text-right">{fmtCurrency(s.endValue)}</TableCell>
              <TableCell className={`text-right ${valueColor(s.vsHigh ?? -1)}`}>{s.vsHigh != null ? fmtPct(s.vsHigh, true) : "N/A"}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

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

  // Filter upcoming earnings to 30 days
  const upcomingEarnings = useMemo(() => {
    if (!r?.holdingsDetail) return [];
    const cutoff = new Date();
    cutoff.setDate(cutoff.getDate() + 30);
    return r.holdingsDetail.upcomingEarnings.filter((s) => {
      if (!s.nextEarnings) return false;
      const d = new Date(s.nextEarnings);
      return d >= new Date() && d <= cutoff;
    });
  }, [r]);

  const NAV_IDS = useMemo(() => ["net-worth", "allocation", "cashflow", "fidelity-activity", "holdings", "market"], []);
  const { active, scrollTo } = useActiveSection(NAV_IDS, !!r);

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

      {/* Data timestamps */}
      {r.metadata && (
        <p className="text-xs text-foreground/50 -mt-4">
          Positions: {r.metadata.positionsDate || "?"} · History: {r.metadata.historyDate || "?"} · Expense Tracker: {r.metadata.qianjiDate || "?"}
        </p>
      )}

      {/* Section nav */}
      <nav className="sticky top-0 z-30 -mx-6 pl-14 md:pl-6 pr-6 py-2 bg-background/80 backdrop-blur-xl backdrop-saturate-150 border-b border-white/20 dark:border-white/8 !rounded-none overflow-x-auto scrollbar-none flex gap-3 text-sm">
        {[
          ["net-worth", "Net Worth"],
          ["allocation", "Allocation"],
          ["cashflow", "Cash Flow"],
          ["fidelity-activity", "Fidelity Activity"],
          ["holdings", "Holdings"],
          ["market", "Market"],
        ].map(([id, label]) => (
          <button
            key={id}
            onClick={() => scrollTo(id)}
            className={`whitespace-nowrap pb-1 transition-colors ${
              active === id
                ? "text-foreground font-medium border-b-2 border-foreground"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {label}
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
        <CategorySummary report={r} />
      </div>

      {/* ── 4. Cash Flow ────────────────────────────────────────────────── */}
      {r.cashflow && (
        <section id="cashflow">
          <SectionHeader>Cash Flow &mdash; {r.cashflow.period}</SectionHeader>
          <SectionBody>
            <CashFlow data={r.cashflow} />
            {r.chartData?.monthlyFlows && r.chartData.monthlyFlows.length > 0 && (
              <div className="mt-6 pt-6 border-t border-border">
                <h3 className="font-semibold mb-2">Income vs Expenses Trend</h3>
                <IncomeExpensesChart data={r.chartData.monthlyFlows} />
              </div>
            )}
            {r.annualSummary && (
              <details className="mt-6 pt-6 border-t border-border">
                <summary className="font-semibold cursor-pointer hover:text-foreground">
                  {r.annualSummary.year} Year-to-Date
                </summary>
                <div className="mt-4"><AnnualSummary data={r.annualSummary} /></div>
              </details>
            )}
          </SectionBody>
        </section>
      )}

      {/* ── 5. Portfolio Activity ───────────────────────────────────────── */}
      {r.activity && (
        <section id="fidelity-activity">
          <SectionHeader>Fidelity Activity</SectionHeader>
          <SectionBody>
            <PortfolioActivity activity={r.activity} reconciliation={r.reconciliation} />
          </SectionBody>
        </section>
      )}

      {/* ── 6. Holdings ──────────────────────────────────────────────────── */}
      {(r.holdingsDetail || r.equityCategories.length > 0) && (
        <section id="holdings">
          <SectionHeader>Holdings Detail</SectionHeader>
          <SectionBody>
            {r.holdingsDetail && (
              <>
                <PerformersTable title="Top Performers" data={r.holdingsDetail.topPerformers} />
                <PerformersTable title="Bottom Performers" data={r.holdingsDetail.bottomPerformers} />
                {upcomingEarnings.length > 0 && (
                  <div>
                    <h3 className="font-semibold mb-2">Upcoming Earnings</h3>
                    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 text-sm">
                      {upcomingEarnings.map((s) => (
                        <div key={s.ticker}>
                          <span className="font-mono font-medium">{s.ticker}</span>
                          <span className="text-muted-foreground"> &mdash; {s.nextEarnings}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            )}
            <GainLoss report={r} />
          </SectionBody>
        </section>
      )}

      {/* ── Market Context ──────────────────────────────────────────────── */}
      {r.market && <div id="market"><MarketContext data={r.market} /></div>}

      <BackToTop />
    </div>
  );
}
