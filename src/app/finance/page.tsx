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
import { InvestmentActivity } from "@/components/finance/investment-activity";
import { BalanceSheet } from "@/components/finance/balance-sheet";
import { MarketContext } from "@/components/finance/market-context";
import { GainLoss } from "@/components/finance/gain-loss";
import { AnnualSummary } from "@/components/finance/annual-summary";
import { NetWorthGrowth } from "@/components/finance/net-worth-growth";
import { Reconciliation } from "@/components/finance/reconciliation";

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
    <div className="max-w-5xl mx-auto space-y-8">
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
          Positions: {r.metadata.positionsDate || "?"} · History: {r.metadata.historyDate || "?"} · Qianji: {r.metadata.qianjiDate || "?"}
        </p>
      )}

      {/* ── 1. Overview ─────────────────────────────────────────────────── */}
      <MetricCards report={r} />

      {/* ── 2. Net Worth ────────────────────────────────────────────────── */}
      <NetWorthGrowth data={r.chartData?.netWorthTrend ?? []} />

      {/* ── 3. Allocation ───────────────────────────────────────────────── */}
      <CategorySummary report={r} />

      {/* ── 4. Cash Flow + Expenses ─────────────────────────────────────── */}
      {r.cashflow && <CashFlow data={r.cashflow} />}

      {r.chartData?.monthlyFlows && r.chartData.monthlyFlows.length > 0 && (
        <section>
          <SectionHeader>Income vs Expenses</SectionHeader>
          <SectionBody>
            <IncomeExpensesChart data={r.chartData.monthlyFlows} />
          </SectionBody>
        </section>
      )}

      {r.annualSummary && <AnnualSummary data={r.annualSummary} />}

      {/* ── 5. Investment Activity ───────────────────────────────────────── */}
      {r.activity && <InvestmentActivity data={r.activity} />}

      {/* ── 6. Balance Sheet + Reconciliation ───────────────────────────── */}
      {r.balanceSheet && <BalanceSheet data={r.balanceSheet} />}
      {r.reconciliation && <Reconciliation data={r.reconciliation} />}

      {/* ── 7. Holdings: Detail + Gain/Loss ─────────────────────────────── */}
      {r.holdingsDetail && (
        <section>
          <SectionHeader>Holdings Detail</SectionHeader>
          <SectionBody>
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
            {r.holdingsDetail.upcomingEarnings.length > 0 && (
              <div>
                <h3 className="font-semibold mb-2">Upcoming Earnings</h3>
                <ul className="space-y-1 text-sm">
                  {r.holdingsDetail.upcomingEarnings.map((s) => (
                    <li key={s.ticker}>
                      <span className="font-mono font-medium">{s.ticker}</span>
                      <span className="text-muted-foreground"> &mdash; {s.nextEarnings}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </SectionBody>
        </section>
      )}

      <GainLoss report={r} />

      {/* ── 8. Market Context ───────────────────────────────────────────── */}
      {r.market && <MarketContext data={r.market} />}
    </div>
  );
}
