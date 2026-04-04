import { sampleReport } from "@/lib/sample-data";
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
  const r = sampleReport;
  const allCategories = [...r.equityCategories, ...r.nonEquityCategories];
  const totalValue = allCategories.reduce((s, c) => s + c.value, 0);
  const totalLots = allCategories.reduce((s, c) => s + c.lots, 0);
  const totalPct = allCategories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = allCategories.reduce((s, c) => s + c.target, 0);
  const totalDeviation = totalPct - totalTarget;

  return (
    <div className="max-w-5xl mx-auto space-y-8">
      {/* Header */}
      <h1 className="text-2xl font-bold tracking-tight">
        Portfolio Snapshot &mdash; {r.date}
      </h1>

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
            <p className="text-2xl font-bold">{fmtCurrency(r.netWorth)}</p>
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
              {Math.round(r.savingsRate)}%
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

      {/* Category Summary */}
      <section>
        <SectionHeader>Category Summary</SectionHeader>
        <SectionBody>
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
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Category</TableHead>
                      <TableHead className="text-right">Count</TableHead>
                      <TableHead className="text-right">Amount</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.cashflow.expenseItems.map((item) => (
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
                        {fmtCurrency(r.cashflow.totalExpenses)}
                      </TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
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
                  <TableHead>Type</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                  <TableHead className="text-right">Amount</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {r.activity.summary.map((row) => (
                  <TableRow key={row.label} className="even:bg-gray-50">
                    <TableCell className="font-medium">{row.label}</TableCell>
                    <TableCell className="text-right">{row.count}</TableCell>
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
              <div>
                <h3 className="font-semibold mb-2">Buys by Ticker</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead>
                      <TableHead className="text-right">Trades</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.activity.buysByTicker.map((t) => (
                      <TableRow key={t.symbol} className="even:bg-gray-50">
                        <TableCell className="font-mono">{t.symbol}</TableCell>
                        <TableCell className="text-right">{t.trades}</TableCell>
                        <TableCell className="text-right">
                          {fmtCurrency(t.total)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div>
                <h3 className="font-semibold mb-2">Dividends by Ticker</h3>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Symbol</TableHead>
                      <TableHead className="text-right">Trades</TableHead>
                      <TableHead className="text-right">Total</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {r.activity.dividendsByTicker.map((t) => (
                      <TableRow key={t.symbol} className="even:bg-gray-50">
                        <TableCell className="font-mono">{t.symbol}</TableCell>
                        <TableCell className="text-right">{t.trades}</TableCell>
                        <TableCell className="text-right">
                          {fmtCurrency(t.total)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
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
                    {r.balanceSheet.assets.map((a) => (
                      <TableRow key={a.name} className="even:bg-gray-50">
                        <TableCell
                          className={a.indent ? "pl-6 text-muted-foreground" : ""}
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
                    {r.balanceSheet.liabilities.map((l) => (
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
