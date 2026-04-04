"use client";

import { useCallback, useEffect, useState } from "react";
import type { ReportData } from "@/lib/types";
import { REPORT_URL } from "@/lib/config";
import { fmtCurrency, fmtCurrencyShort, fmtPct, fmtYuan } from "@/lib/format";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AllocationDonut, IncomeExpensesChart, NetWorthTrendChart } from "@/components/finance/charts";

const MAJOR_EXPENSE_THRESHOLD = 200;
const ACTIVITY_TOP_SYMBOLS = 5;

function TickerTable({
  title,
  data,
}: {
  title: string;
  data: [string, number, number][]; // [symbol, trades, total]
}) {
  const top = data.slice(0, ACTIVITY_TOP_SYMBOLS);
  const rest = data.slice(ACTIVITY_TOP_SYMBOLS);
  const restTotal = rest.reduce((s, t) => s + t[2], 0);
  return (
    <div>
      <h3 className="font-semibold mb-2">{title}</h3>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Symbol</TableHead>
            <TableHead className="text-right">Trades</TableHead>
            <TableHead className="text-right">Total</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {top.map(([symbol, trades, total]) => (
            <TableRow key={symbol} className="even:bg-gray-50">
              <TableCell className="font-mono">{symbol}</TableCell>
              <TableCell className="text-right">{trades}</TableCell>
              <TableCell className="text-right">
                {fmtCurrency(total)}
              </TableCell>
            </TableRow>
          ))}
          {rest.length > 0 && (
            <TableRow>
              <TableCell colSpan={3} className="p-0">
                <details className="group">
                  <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                    ... and {rest.length} more ({fmtCurrency(restTotal)})
                  </summary>
                  <table className="w-full text-sm">
                    <tbody>
                      {rest.map(([symbol, trades, total]) => (
                        <tr
                          key={symbol}
                          className="border-b border-gray-100 even:bg-gray-50"
                        >
                          <td className="px-2 py-1.5 font-mono text-muted-foreground">
                            {symbol}
                          </td>
                          <td className="px-2 py-1.5 text-right text-muted-foreground">
                            {trades}
                          </td>
                          <td className="px-2 py-1.5 text-right text-muted-foreground">
                            {fmtCurrency(total)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </details>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[#16213e] text-white px-4 py-2.5 rounded-t-md font-bold">
      {children}
    </div>
  );
}

function SectionBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="border border-gray-200 rounded-b-md p-4">{children}</div>
  );
}

function DeviationCell({ value }: { value: number }) {
  return (
    <TableCell
      className={`text-right ${value >= 0 ? "text-green-600" : "text-red-500"}`}
    >
      {fmtPct(value)}
    </TableCell>
  );
}

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

  const allCategories = [...r.equityCategories, ...r.nonEquityCategories];
  const totalValue = allCategories.reduce((s, c) => s + c.value, 0);
  const totalPct = allCategories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = allCategories.reduce((s, c) => s + c.target, 0);
  const totalDeviation = totalPct - totalTarget;

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold tracking-tight">
          Portfolio Snapshot &mdash; {r.date}
        </h1>
        <Button onClick={fetchReport} variant="outline" size="sm" disabled={loading}>
          {loading ? "Loading..." : "Reload"}
        </Button>
      </div>

      {/* Metric Cards Row */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">
              Portfolio
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fmtCurrency(r.total)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">
              Net Worth
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">{fmtCurrency(r.balanceSheet?.netWorth ?? r.total)}</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">
              Savings Rate
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold text-green-600">
              {r.cashflow ? `${Math.round(r.cashflow.savingsRate)}%` : "N/A"}
            </p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-sm text-muted-foreground">
              Goal
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-2xl font-bold">
              {Math.round(r.goalPct)}% of {fmtCurrencyShort(r.goal)}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Net Worth Trend */}
      {r.chartData?.netWorthTrend && r.chartData.netWorthTrend.length > 0 && (
        <section>
          <SectionHeader>Portfolio Trend</SectionHeader>
          <SectionBody>
            <NetWorthTrendChart data={r.chartData.netWorthTrend} />
          </SectionBody>
        </section>
      )}

      {/* Category Summary */}
      <section>
        <SectionHeader>Category Summary</SectionHeader>
        <SectionBody>
          <div className="flex flex-col lg:flex-row gap-6">
          <div className="flex-1 min-w-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Category</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead className="text-right">Actual</TableHead>
                <TableHead className="text-right">Target</TableHead>
                <TableHead className="text-right">Deviation</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {/* Equity categories */}
              {r.equityCategories.map((cat) => (
                <>
                  <TableRow key={cat.name} className="even:bg-gray-50">
                    <TableCell className="font-medium">{cat.name}</TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(cat.value)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPct(cat.pct, false)}
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtPct(cat.target, false)}
                    </TableCell>
                    <DeviationCell value={cat.deviation} />
                  </TableRow>
                  {cat.subtypes.map((sub) => (
                    <TableRow
                      key={`${cat.name}-${sub.name}`}
                      className="even:bg-gray-50"
                    >
                      <TableCell className="text-muted-foreground">
                        &nbsp;&nbsp;
                        <em>{sub.name}</em>
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {fmtCurrency(sub.value)}
                      </TableCell>
                      <TableCell className="text-right text-muted-foreground">
                        {fmtPct(sub.pct, false)}
                      </TableCell>
                      <TableCell />
                      <TableCell />
                    </TableRow>
                  ))}
                </>
              ))}

              {/* Non-Equity group header */}
              <TableRow className="bg-gray-100">
                <TableCell
                  colSpan={5}
                  className="font-semibold text-muted-foreground"
                >
                  Non-Equity
                </TableCell>
              </TableRow>
              {r.nonEquityCategories.map((cat) => (
                <TableRow key={cat.name} className="even:bg-gray-50">
                  <TableCell className="font-medium">{cat.name}</TableCell>
                  <TableCell className="text-right">
                    {fmtCurrency(cat.value)}
                  </TableCell>
                  <TableCell className="text-right">
                    {fmtPct(cat.pct, false)}
                  </TableCell>
                  <TableCell className="text-right">
                    {fmtPct(cat.target, false)}
                  </TableCell>
                  <DeviationCell value={cat.deviation} />
                </TableRow>
              ))}

              {/* Total row */}
              <TableRow className="font-bold border-t-2 border-b-2 border-gray-800">
                <TableCell>Total</TableCell>
                <TableCell className="text-right">
                  {fmtCurrency(totalValue)}
                </TableCell>
                <TableCell className="text-right">
                  {fmtPct(totalPct, false)}
                </TableCell>
                <TableCell className="text-right">
                  {fmtPct(totalTarget, false)}
                </TableCell>
                <DeviationCell value={totalDeviation} />
              </TableRow>
            </TableBody>
          </Table>
          <p className="mt-3 text-sm text-muted-foreground">
            {r.goalPct.toFixed(2)}% of {fmtCurrency(r.goal)} goal
          </p>
          </div>
          <div className="lg:w-80 flex-shrink-0">
            <AllocationDonut categories={allCategories} total={totalValue} />
          </div>
          </div>
        </SectionBody>
      </section>

      {/* Cash Flow */}
      {r.cashflow && (
        <section>
          <SectionHeader>Cash Flow &mdash; {r.cashflow.period}</SectionHeader>
          <SectionBody>
            <div className="grid md:grid-cols-2 gap-6">
              {/* Income */}
              <div>
                <h3 className="font-semibold mb-2">Income</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Category</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.cashflow.incomeItems.map((item) => (
                      <TableRow
                        key={item.category}
                        className="even:bg-gray-50"
                      >
                        <TableCell>{item.category}</TableCell>
                        <TableCell className="text-right">
                          {item.count}
                        </TableCell>
                        <TableCell className="text-right">
                          {fmtCurrency(item.amount)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-bold border-t-2 border-b-2 border-gray-800">
                      <TableCell>Total</TableCell>
                      <TableCell />
                      <TableCell className="text-right">
                        {fmtCurrency(r.cashflow.totalIncome)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>

              {/* Expenses */}
              <div>
                <h3 className="font-semibold mb-2">Expenses</h3>
                {(() => {
                  const major = r.cashflow!.expenseItems.filter(
                    (i) => i.amount >= MAJOR_EXPENSE_THRESHOLD
                  );
                  const minor = r.cashflow!.expenseItems.filter(
                    (i) => i.amount < MAJOR_EXPENSE_THRESHOLD
                  );
                  const minorTotal = minor.reduce(
                    (s, i) => s + i.amount,
                    0
                  );
                  return (
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead>Category</TableHead>
                          <TableHead className="text-right">Count</TableHead>
                          <TableHead className="text-right">Amount</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {major.map((item) => (
                          <TableRow
                            key={item.category}
                            className="even:bg-gray-50"
                          >
                            <TableCell>{item.category}</TableCell>
                            <TableCell className="text-right">
                              {item.count}
                            </TableCell>
                            <TableCell className="text-right">
                              {fmtCurrency(item.amount)}
                            </TableCell>
                          </TableRow>
                        ))}
                        {minor.length > 0 && (
                          <TableRow>
                            <TableCell colSpan={3} className="p-0">
                              <details className="group">
                                <summary className="px-2 py-1.5 text-sm text-muted-foreground cursor-pointer hover:text-foreground">
                                  ... and {minor.length} more ({fmtCurrency(minorTotal)})
                                </summary>
                                <table className="w-full text-sm">
                                  <tbody>
                                    {minor.map((item) => (
                                      <tr
                                        key={item.category}
                                        className="border-b border-gray-100 even:bg-gray-50"
                                      >
                                        <td className="px-2 py-1.5 text-muted-foreground">
                                          {item.category}
                                        </td>
                                        <td className="px-2 py-1.5 text-right text-muted-foreground">
                                          {item.count}
                                        </td>
                                        <td className="px-2 py-1.5 text-right text-muted-foreground">
                                          {fmtCurrency(item.amount)}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </details>
                            </TableCell>
                          </TableRow>
                        )}
                        <TableRow className="font-bold border-t-2 border-b-2 border-gray-800">
                          <TableCell>Total</TableCell>
                          <TableCell />
                          <TableCell className="text-right">
                            {fmtCurrency(r.cashflow!.totalExpenses)}
                          </TableCell>
                        </TableRow>
                      </TableBody>
                    </Table>
                  );
                })()}
              </div>
            </div>

            {/* Cash Flow Summary */}
            <div className="mt-6">
              <h3 className="font-semibold mb-2">Summary</h3>
              <Table>
                <TableBody>
                  <TableRow className="even:bg-gray-50">
                    <TableCell className="font-medium">Net Cash Flow</TableCell>
                    <TableCell className="text-right text-green-600 font-semibold">
                      {fmtCurrency(r.cashflow.netCashflow)}
                    </TableCell>
                  </TableRow>
                  <TableRow className="even:bg-gray-50">
                    <TableCell className="font-medium">Invested</TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(r.cashflow.invested)}
                    </TableCell>
                  </TableRow>
                  <TableRow className="even:bg-gray-50">
                    <TableCell className="font-medium">CC Payments</TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(r.cashflow.creditCardPayments)}
                    </TableCell>
                  </TableRow>
                  <TableRow className="even:bg-gray-50">
                    <TableCell className="font-medium">
                      Gross Savings Rate
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant="secondary">
                        {r.cashflow.savingsRate.toFixed(1)}%
                      </Badge>
                    </TableCell>
                  </TableRow>
                  <TableRow className="even:bg-gray-50">
                    <TableCell className="font-medium">
                      Take-home Savings Rate
                    </TableCell>
                    <TableCell className="text-right">
                      <Badge variant="secondary">
                        {r.cashflow.takehomeSavingsRate.toFixed(1)}%
                      </Badge>
                    </TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </SectionBody>
        </section>
      )}

      {/* Income vs Expenses Chart */}
      {r.chartData?.monthlyFlows && r.chartData.monthlyFlows.length > 0 && (
        <section>
          <SectionHeader>Income vs Expenses</SectionHeader>
          <SectionBody>
            <IncomeExpensesChart data={r.chartData.monthlyFlows} />
          </SectionBody>
        </section>
      )}

      {/* Investment Activity */}
      {r.activity && (
        <section>
          <SectionHeader>Investment Activity</SectionHeader>
          <SectionBody>
            <p className="text-sm text-muted-foreground mb-4">
              {r.activity.periodStart} &ndash; {r.activity.periodEnd}
            </p>

            {/* Activity Summary */}
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Metric</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {[
                  { label: "Net Cash In", amount: r.activity.netCashIn },
                  { label: "Net Deployed", amount: r.activity.netDeployed },
                  { label: "Net Passive Income", amount: r.activity.netPassive },
                  { label: "Reinvestments", amount: r.activity.reinvestmentsTotal },
                  { label: "Interest", amount: r.activity.interestTotal },
                  { label: "Foreign Tax", amount: r.activity.foreignTaxTotal },
                ].filter((row) => row.amount !== 0).map((row) => (
                  <TableRow key={row.label} className="even:bg-gray-50">
                    <TableCell className="font-medium">{row.label}</TableCell>
                    <TableCell
                      className={`text-right ${row.amount >= 0 ? "text-green-600" : "text-red-500"}`}
                    >
                      {fmtCurrency(row.amount)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>

            {/* Buys by Ticker and Dividends by Ticker */}
            <div className="grid md:grid-cols-2 gap-6 mt-6">
              <TickerTable
                title="Buys by Symbol"
                data={r.activity.buysBySymbol}
              />
              <TickerTable
                title="Dividends by Symbol"
                data={r.activity.dividendsBySymbol}
              />
            </div>
          </SectionBody>
        </section>
      )}

      {/* Balance Sheet */}
      {r.balanceSheet && (
        <section>
          <SectionHeader>Balance Sheet</SectionHeader>
          <SectionBody>
            <div className="grid md:grid-cols-2 gap-6">
              {/* Assets */}
              <div>
                <h3 className="font-semibold mb-2">Assets</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Account</TableHead>
                      <TableHead className="text-right">Balance</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow className="even:bg-gray-50">
                      <TableCell className="font-medium">Investments (Fidelity)</TableCell>
                      <TableCell className="text-right">
                        {fmtCurrency(r.balanceSheet.investmentTotal)}
                      </TableCell>
                    </TableRow>
                    {r.balanceSheet.accounts.map((a) => (
                      <TableRow key={a.name} className="even:bg-gray-50">
                        <TableCell
                          className={a.currency === "CNY" ? "pl-6 text-muted-foreground" : ""}
                        >
                          {a.name}
                        </TableCell>
                        <TableCell className="text-right">
                          {a.currency === "CNY"
                            ? fmtYuan(a.balance)
                            : fmtCurrency(a.balance)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-bold border-t-2 border-b-2 border-gray-800">
                      <TableCell>Total Assets</TableCell>
                      <TableCell className="text-right">
                        {fmtCurrency(r.balanceSheet.totalAssets)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>

              {/* Liabilities */}
              <div>
                <h3 className="font-semibold mb-2">Liabilities</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Account</TableHead>
                      <TableHead className="text-right">Balance</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.balanceSheet.creditCards.map((l) => (
                      <TableRow key={l.name} className="even:bg-gray-50">
                        <TableCell>{l.name}</TableCell>
                        <TableCell className="text-right text-red-500">
                          {fmtCurrency(l.balance)}
                        </TableCell>
                      </TableRow>
                    ))}
                    <TableRow className="font-bold border-t-2 border-b-2 border-gray-800">
                      <TableCell>Total Liabilities</TableCell>
                      <TableCell className="text-right text-red-500">
                        {fmtCurrency(r.balanceSheet.totalLiabilities)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>
            </div>

            {/* Net Worth total */}
            <div className="mt-4 flex justify-between items-center px-2 py-3 border-t-2 border-b-2 border-gray-800 font-bold text-lg">
              <span>Net Worth</span>
              <span>{fmtCurrency(r.balanceSheet.netWorth)}</span>
            </div>
          </SectionBody>
        </section>
      )}
    </div>
  );
}
