"use client";

// ── Group chart dialog: mirrors TickerChartDialog for equivalent-ticker groups ──

import { GroupChart, buildGroupChartData } from "./group-chart";
import { ChartDialog } from "./chart-dialog";
import { buildGroupValueSeries, groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS } from "@/lib/config/equivalent-groups";
import { fmtCurrency, fmtPct } from "@/lib/format/format";
import type { DailyTicker, FidelityTxn } from "@/lib/schemas";

export function GroupChartDialog({
  groupKey,
  dailyTickers,
  fidelityTxns,
  startDate,
  endDate,
  onClose,
  onSelectTicker,
}: {
  groupKey: string;
  dailyTickers: DailyTicker[];
  fidelityTxns: FidelityTxn[];
  startDate?: string;
  endDate?: string;
  onClose: () => void;
  onSelectTicker?: (symbol: string) => void;
}) {
  const group = EQUIVALENT_GROUPS[groupKey];
  if (!group) return null;

  const series = buildGroupValueSeries(dailyTickers, group.tickers)
    .filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate));
  const markers = groupNetByDate(fidelityTxns).get(groupKey) ?? new Map();
  const data = buildGroupChartData(series, markers);
  const latest = series[series.length - 1];

  const header = (
    <div className="flex items-baseline gap-3 min-w-0 flex-wrap">
      <span className="font-semibold text-lg truncate">{group.display}</span>
      {latest && (
        <span className="text-sm text-muted-foreground">value {fmtCurrency(latest.value)}</span>
      )}
      {latest && latest.value > 0 ? (
        <span className="text-xs text-muted-foreground truncate flex gap-2">
          {latest.constituents
            .slice()
            .sort((a, b) => b.value - a.value)
            .map((c, i, arr) => (
              <span key={c.ticker} className="inline-flex items-center gap-1">
                {onSelectTicker ? (
                  <button
                    type="button"
                    className="underline decoration-dotted underline-offset-2 hover:text-foreground transition-colors cursor-pointer"
                    onClick={() => onSelectTicker(c.ticker)}
                  >
                    {c.ticker}
                  </button>
                ) : (
                  <span>{c.ticker}</span>
                )}
                <span>{fmtPct((c.value / latest.value) * 100, false)}</span>
                {i < arr.length - 1 && <span aria-hidden>·</span>}
              </span>
            ))}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground truncate">{group.tickers.join(" · ")}</span>
      )}
    </div>
  );

  return (
    <ChartDialog header={header} onClose={onClose}>
      <div className="flex-1 min-h-0 px-4 pt-4 pb-4">
        <GroupChart data={data} />
      </div>
    </ChartDialog>
  );
}
