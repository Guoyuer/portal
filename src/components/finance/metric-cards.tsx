import type { ReportData } from "@/lib/types";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format";
import { savingsRateColor } from "@/lib/style-helpers";

export function MetricCards({ report: r }: { report: ReportData }) {
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-sm text-muted-foreground">Investments</p>
        <p className="text-2xl font-bold mt-1">{fmtCurrency(r.total)}</p>
      </div>
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-sm text-muted-foreground">Net Worth</p>
        <p className="text-2xl font-bold mt-1">{fmtCurrency(r.balanceSheet?.netWorth ?? r.total)}</p>
      </div>
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-xs sm:text-sm text-muted-foreground">Savings Rate</p>
        {r.cashflow ? (
          <div className="mt-1">
            <p className={`text-xl sm:text-2xl font-bold ${savingsRateColor(r.cashflow.savingsRate)}`}>
              {Math.round(r.cashflow.savingsRate)}%
            </p>
            <p className="text-xs text-muted-foreground">
              {Math.round(r.cashflow.takehomeSavingsRate)}% take-home
            </p>
          </div>
        ) : (
          <p className="text-xl sm:text-2xl font-bold mt-1">N/A</p>
        )}
      </div>
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-xs sm:text-sm text-muted-foreground">Goal</p>
        <p className="text-xl sm:text-2xl font-bold mt-1">
          {Math.round(r.goalPct)}%
          <span className="text-sm font-normal text-muted-foreground ml-1">of {fmtCurrencyShort(r.goal)}</span>
        </p>
        <div className="mt-2 h-2 w-full rounded-full bg-black/5 dark:bg-white/10">
          <div
            className="h-2 rounded-full bg-blue-500 transition-all"
            style={{ width: `${Math.min(r.goalPct, 100)}%` }}
          />
        </div>
      </div>
    </div>
  );
}
