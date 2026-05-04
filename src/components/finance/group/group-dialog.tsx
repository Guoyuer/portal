"use client";

// ── Group chart dialog: mirrors TickerChartDialog for equivalent-ticker groups ──

import { useState } from "react";
import { useHoverState } from "@/lib/hooks/use-hover-state";
import { GroupChart, buildGroupChartData } from "./group-chart";
import { ChartDialog } from "../charts/chart-dialog";
import { buildGroupValueSeries, groupNetByDate } from "@/lib/data/group-aggregation";
import { EQUIVALENT_GROUPS } from "@/lib/data/equivalent-groups";
import { fmtCurrency, fmtPct } from "@/lib/format/format";
import type { DailyTicker } from "@/lib/schemas/timeline";
import type { TickerTxn } from "@/lib/schemas/ticker";
import type { InvestmentTxn } from "@/lib/compute/compute";
import { TransactionTable } from "../transaction-table";
import { MarkerHoverPanel } from "../charts/marker-hover-panel";
import { useIsDark } from "@/lib/hooks/use-is-dark";
import type { Selection } from "../ticker/ticker-markers";
import { useTickerData } from "../ticker/ticker-chart";

type EquivalentGroup = (typeof EQUIVALENT_GROUPS)[string];

type GroupChartDialogProps = {
  groupKey: string;
  dailyTickers: DailyTicker[];
  investmentTxns: InvestmentTxn[];
  startDate?: string;
  endDate?: string;
  onClose: () => void;
  onSelectTicker?: (symbol: string) => void;
};

// ── Adapter: normalized investment rows → chart-dialog transaction rows ──
function investmentTxnsToTickerTxns(txns: InvestmentTxn[], tickers: string[]): TickerTxn[] {
  const set = new Set(tickers);
  return txns
    .filter((t) => set.has(t.ticker))
    .filter((t) => t.actionType === "buy" || t.actionType === "sell" || t.actionType === "reinvestment" || t.actionType === "contribution")
    .map((t) => ({
      runDate: t.date,
      actionType: t.actionType,
      quantity: t.quantity ?? 0,
      price: t.price ?? 0,
      amount: t.amount,
    }));
}

export function GroupChartDialog({
  groupKey,
  ...props
}: GroupChartDialogProps) {
  const group = EQUIVALENT_GROUPS[groupKey];
  if (!group) return null;

  return <GroupChartDialogContent groupKey={groupKey} group={group} {...props} />;
}

function GroupChartDialogContent({
  groupKey,
  group,
  dailyTickers,
  investmentTxns,
  startDate,
  endDate,
  onClose,
  onSelectTicker,
}: GroupChartDialogProps & { group: EquivalentGroup }) {
  const isDark = useIsDark();
  const [selected, setSelected] = useState<Selection | null>(null);
  const { hover, onEnter, onMove, onLeave } = useHoverState();

  // Fetch proxy ticker price series. The group markers below come from the
  // same normalized investment transactions that power grouped activity rows.
  const proxy = useTickerData(group.representative);

  const valueSeries = buildGroupValueSeries(dailyTickers, group.tickers)
    .filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate));
  const latestValue = valueSeries[valueSeries.length - 1];

  const markers = groupNetByDate(investmentTxns).get(groupKey) ?? new Map();

  const sorted = investmentTxnsToTickerTxns(investmentTxns, group.tickers)
    .sort((a, b) => b.runDate.localeCompare(a.runDate));

  const header = (
    <div className="flex items-baseline gap-3 min-w-0 flex-wrap">
      <span className="font-semibold text-lg truncate">{group.display}</span>
      {latestValue && (
        <span className="text-sm text-muted-foreground">Holdings {fmtCurrency(latestValue.value)}</span>
      )}
      {latestValue && latestValue.value > 0 ? (
        <span className="text-xs text-muted-foreground truncate flex gap-2">
          {latestValue.constituents
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
                <span>{fmtPct((c.value / latestValue.value) * 100, false)}</span>
                {i < arr.length - 1 && <span aria-hidden>·</span>}
              </span>
            ))}
        </span>
      ) : (
        <span className="text-xs text-muted-foreground truncate">{group.tickers.join(" · ")}</span>
      )}
    </div>
  );

  // Build chart data once proxy prices are available
  const chartContent = (() => {
    if (proxy.status === "error") {
      return (
        <p className="text-sm text-red-400 py-4 px-4">
          Failed to load proxy price chart: {proxy.error}
        </p>
      );
    }
    if (proxy.status === "loading") {
      return (
        <p className="text-sm text-muted-foreground py-4 px-4 animate-pulse">
          Loading {group.representative} price...
        </p>
      );
    }
    if (proxy.status === "pseudo" || proxy.status === "missing" || proxy.status === "empty") {
      return (
        <p className="text-sm text-muted-foreground py-4 px-4">
          No price data for {group.representative}
        </p>
      );
    }
    // Filter price series to brush range
    const filteredPrices = (startDate || endDate)
      ? proxy.data.filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate))
      : proxy.data;

    if (filteredPrices.length === 0) {
      return (
        <p className="text-sm text-muted-foreground py-4 px-4">
          No price data for {group.representative}
        </p>
      );
    }

    const priceMap = new Map(filteredPrices.map((p) => [p.date, p.close]));
    const chartData = buildGroupChartData(priceMap, markers);

    return (
      <GroupChart
        data={chartData}
        representative={group.representative}
        onEnter={onEnter}
        onMove={onMove}
        onLeave={onLeave}
        onSelect={setSelected}
        selectedKey={selected?.key ?? null}
        tooltipWrapperStyle={hover ? { visibility: "hidden" } : undefined}
      />
    );
  })();

  return (
    <ChartDialog header={header} onClose={onClose}>
      <div className="flex-1 min-h-0 px-4 pt-4 pb-2">
        {chartContent}
        {hover && <MarkerHoverPanel hover={hover} isDark={isDark} valueLabel={group.representative} />}
      </div>
      <TransactionTable
        transactions={sorted}
        selected={selected}
      />
    </ChartDialog>
  );
}
