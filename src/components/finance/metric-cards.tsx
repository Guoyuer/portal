import type { ReportData } from "@/lib/types";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format";
import { savingsRateColor } from "@/lib/style-helpers";

export function MetricCards({
  report: r,
  allocationOpen,
  onAllocationToggle,
}: {
  report: ReportData;
  allocationOpen: boolean;
  onAllocationToggle: () => void;
}) {
  const allCats = [...r.equityCategories, ...r.nonEquityCategories];
  const cashCategories = new Set(["Safe Net"]);
  const safeNetValue = allCats.filter((c) => cashCategories.has(c.name)).reduce((s, c) => s + c.value, 0);
  const investmentValue = allCats.reduce((s, c) => s + c.value, 0) - safeNetValue;
  const netWorth = r.balanceSheet?.netWorth ?? r.total;
  const invPct = netWorth > 0 ? (investmentValue / netWorth) * 100 : 0;

  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
      <button
        type="button"
        data-slot="card"
        className="liquid-glass p-4 col-span-2 text-left cursor-pointer hover:ring-1 hover:ring-foreground/10 transition-shadow"
        onClick={onAllocationToggle}
      >
        <div className="flex items-baseline justify-between">
          <p className="text-sm text-muted-foreground">Net Worth</p>
          <p className="text-2xl font-bold tabular-nums">{fmtCurrency(netWorth)}</p>
        </div>
        <div className="mt-2 flex h-2 w-full rounded-full overflow-hidden">
          <div className="h-2 bg-cyan-400 dark:bg-cyan-400 transition-all" style={{ width: `${100 - invPct}%` }} />
          <div className="h-2 bg-blue-500 flex-1" />
        </div>
        <div className="mt-2 flex justify-between text-xs">
          <div>
            <span className="inline-block w-2 h-2 rounded-sm bg-cyan-400 dark:bg-cyan-400 mr-1.5 align-middle" />
            <span className="text-muted-foreground">Safe Net {Math.round(100 - invPct)}%</span>
            <p className="text-base font-semibold tabular-nums mt-0.5">{fmtCurrencyShort(safeNetValue)}</p>
          </div>
          <div className="text-right">
            <span className="text-muted-foreground">{Math.round(invPct)}% Investment</span>
            <span className="inline-block w-2 h-2 rounded-sm bg-blue-500 ml-1.5 align-middle" />
            <p className="text-base font-semibold tabular-nums mt-0.5">{fmtCurrencyShort(investmentValue)}</p>
          </div>
        </div>
        <div className="mt-2 flex justify-center">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
            className={`h-4 w-4 text-muted-foreground transition-transform duration-300 ${allocationOpen ? "rotate-180" : ""}`}
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </div>
      </button>
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
        <p className="text-xl sm:text-2xl font-bold mt-1">{Math.round(r.goalPct)}% <span className="text-xs font-normal text-muted-foreground">of ${Math.round(r.goal / 1_000_000)}M</span></p>
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
