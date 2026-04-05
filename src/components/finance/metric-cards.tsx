import type { ReportData } from "@/lib/types";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format";
import { savingsRateColor } from "@/lib/style-helpers";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function MetricCards({ report: r }: { report: ReportData }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <Card>
        <CardHeader>
          <CardTitle className="text-sm text-muted-foreground">
            Investments
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
            Monthly Savings Rate
          </CardTitle>
        </CardHeader>
        <CardContent>
          {r.cashflow ? (
            <div>
              <p className={`text-2xl font-bold ${savingsRateColor(r.cashflow.savingsRate)}`}>
                {Math.round(r.cashflow.savingsRate)}%
              </p>
              <p className="text-xs text-muted-foreground">
                {Math.round(r.cashflow.takehomeSavingsRate)}% take-home
              </p>
            </div>
          ) : (
            <p className="text-2xl font-bold">N/A</p>
          )}
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
          <div className="mt-2 h-2 w-full rounded-full bg-muted">
            <div
              className="h-2 rounded-full bg-blue-600 transition-all"
              style={{ width: `${Math.min(r.goalPct, 100)}%` }}
            />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
