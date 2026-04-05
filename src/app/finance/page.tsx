"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReportData } from "@/lib/types";
import { REPORT_URL } from "@/lib/config";
import { fmtCurrency, fmtPct } from "@/lib/format";
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
import { BalanceSheet } from "@/components/finance/balance-sheet";
import { MarketContext } from "@/components/finance/market-context";
import { GainLoss } from "@/components/finance/gain-loss";
import { AnnualSummary } from "@/components/finance/annual-summary";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { BackToTop } from "@/components/layout/back-to-top";

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
      setReport(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load report");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchReport(); }, [fetchReport]);

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
      <div className="flex items-start sm:items-center justify-between gap-2">
        <h1 className="text-xl sm:text-2xl font-bold tracking-tight">
          Portfolio Snapshot &mdash; {r.date}
        </h1>
        <Button onClick={fetchReport} variant="outline" size="sm" disabled={loading} className="flex-shrink-0">
          {loading ? "Loading..." : "Reload"}
        </Button>
      </div>

      {/* Data timestamps */}
      {r.metadata && (
        <p className="text-xs text-muted-foreground -mt-4">
          Positions: {r.metadata.positionsDate || "?"} · History: {r.metadata.historyDate || "?"} · Expense Tracker: {r.metadata.qianjiDate || "?"}
        </p>
      )}

      {/* Section nav */}
      <nav className="sticky top-0 z-40 -mx-4 px-4 py-2 bg-background/95 backdrop-blur border-b border-border overflow-x-auto flex gap-3 text-sm">
        {[
          ["net-worth", "Net Worth"],
          ["allocation", "Allocation"],
          ["cashflow", "Cash Flow"],
          ["portfolio-activity", "Activity"],
          ["balance-sheet", "Balance Sheet"],
          ["holdings", "Holdings"],
          ["market", "Market"],
        ].map(([id, label]) => (
          <a
            key={id}
            href={`#${id}`}
            onClick={(e) => {
              e.preventDefault();
              document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
            }}
            className="whitespace-nowrap text-muted-foreground hover:text-foreground transition-colors"
          >
            {label}
          </a>
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

      {/* ── 4. Cash Flow (monthly + trend chart + YTD) ──────────────────── */}
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
                <div className="mt-4">
                  <AnnualSummary data={r.annualSummary} />
                </div>
              </details>
            )}
          </SectionBody>
        </section>
      )}

      {/* ── 5. Portfolio Activity (merged: activity + reconciliation) ──── */}
      {r.activity && (
        <section id="portfolio-activity">
          <SectionHeader>Portfolio Activity</SectionHeader>
          <SectionBody>
            <PortfolioActivity activity={r.activity} reconciliation={r.reconciliation} />
          </SectionBody>
        </section>
      )}

      {/* ── 6. Balance Sheet ────────────────────────────────────────────── */}
      {r.balanceSheet && <div id="balance-sheet"><BalanceSheet data={r.balanceSheet} /></div>}

      {/* ── 7. Holdings (performers + earnings + gain/loss) ─────────────── */}
      {(r.holdingsDetail || r.equityCategories.length > 0) && (
        <section id="holdings">
          <SectionHeader>Holdings</SectionHeader>
          <SectionBody>
            {r.holdingsDetail && (
              <>
                {r.holdingsDetail.topPerformers.length > 0 && (
                  <div className="mb-6">
                    <h3 className="font-semibold mb-2">Top Performers</h3>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Ticker</TableHead>
                          <TableHead className="text-right">Month Return</TableHead>
                          <TableHead className="text-right">Value</TableHead>
                          <TableHead className="text-right">vs 52W High</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {r.holdingsDetail.topPerformers.slice(0, 5).map((s) => (
                          <TableRow key={s.ticker} className="even:bg-muted/50">
                            <TableCell className="font-mono">{s.ticker}</TableCell>
                            <TableCell
                              className={`text-right ${s.monthReturn >= 0 ? "text-green-600" : "text-red-500"}`}
                            >
                              {fmtPct(s.monthReturn)}
                            </TableCell>
                            <TableCell className="text-right">
                              {fmtCurrency(s.endValue)}
                            </TableCell>
                            <TableCell
                              className={`text-right ${s.vsHigh != null && s.vsHigh >= 0 ? "text-green-600" : "text-red-500"}`}
                            >
                              {s.vsHigh != null ? fmtPct(s.vsHigh) : "N/A"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
                {r.holdingsDetail.bottomPerformers.length > 0 && (
                  <div className="mb-6">
                    <h3 className="font-semibold mb-2">Bottom Performers</h3>
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Ticker</TableHead>
                          <TableHead className="text-right">Month Return</TableHead>
                          <TableHead className="text-right">Value</TableHead>
                          <TableHead className="text-right">vs 52W High</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {r.holdingsDetail.bottomPerformers.slice(0, 5).map((s) => (
                          <TableRow key={s.ticker} className="even:bg-muted/50">
                            <TableCell className="font-mono">{s.ticker}</TableCell>
                            <TableCell
                              className={`text-right ${s.monthReturn >= 0 ? "text-green-600" : "text-red-500"}`}
                            >
                              {fmtPct(s.monthReturn)}
                            </TableCell>
                            <TableCell className="text-right">
                              {fmtCurrency(s.endValue)}
                            </TableCell>
                            <TableCell
                              className={`text-right ${s.vsHigh != null && s.vsHigh >= 0 ? "text-green-600" : "text-red-500"}`}
                            >
                              {s.vsHigh != null ? fmtPct(s.vsHigh) : "N/A"}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                )}
                {(() => {
                  const now = Date.now();
                  const thirtyDays = 30 * 24 * 60 * 60 * 1000;
                  const soon = r.holdingsDetail!.upcomingEarnings.filter((s) => {
                    if (!s.nextEarnings) return false;
                    const d = new Date(s.nextEarnings).getTime();
                    return !isNaN(d) && d >= now && d <= now + thirtyDays;
                  });
                  if (soon.length === 0) return null;
                  return (
                    <div className="mb-6">
                      <h3 className="font-semibold mb-2">Upcoming Earnings (30 days)</h3>
                      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2 text-sm">
                        {soon.map((s) => (
                          <div key={s.ticker}>
                            <span className="font-mono font-medium">{s.ticker}</span>
                            <span className="text-muted-foreground"> &mdash; {s.nextEarnings}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })()}
              </>
            )}
            <GainLoss report={r} />
          </SectionBody>
        </section>
      )}

      {/* ── 8. Market Context ───────────────────────────────────────────── */}
      {r.market && <div id="market"><MarketContext data={r.market} /></div>}

      <BackToTop />
    </div>
  );
}
