"use client";

// ── Group chart dialog: mirrors TickerChartDialog for equivalent-ticker groups ──

import { useEffect, useRef, useState } from "react";
import { GroupChart, buildGroupChartData } from "./group-chart";
import { ChartDialog } from "./chart-dialog";
import { buildGroupValueSeries, groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS } from "@/lib/config/equivalent-groups";
import { fmtCurrency, fmtPct } from "@/lib/format/format";
import type { DailyTicker, FidelityTxn } from "@/lib/schemas";
import type { TickerTransaction } from "@/lib/schemas";
import { TransactionTable } from "./transaction-table";
import { MarkerHoverPanel } from "./marker-hover-panel";
import { useIsDark } from "@/lib/hooks/hooks";
import type { HoverState, Selection } from "./ticker-markers";

// ── Adapter: FidelityTxn rows → TickerTransaction (shapes are identical) ──
function fidelityTxnsToTickerTransactions(txns: FidelityTxn[], tickers: string[]): TickerTransaction[] {
  const set = new Set(tickers);
  return txns
    .filter((t) => set.has(t.symbol))
    .map((t) => ({
      runDate: t.runDate,
      actionType: t.actionType,
      quantity: t.quantity,
      price: t.price,
      amount: t.amount,
    }));
}

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

  const isDark = useIsDark();
  const [selected, setSelected] = useState<Selection | null>(null);
  const [hover, setHover] = useState<HoverState | null>(null);
  const tableScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!selected || !tableScrollRef.current) return;
    const cell = tableScrollRef.current.querySelector<HTMLElement>(
      `td[data-date="${selected.dates[0]}"][data-side="${selected.side}"]`,
    );
    cell?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [selected]);

  const series = buildGroupValueSeries(dailyTickers, group.tickers)
    .filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate));
  const markers = groupNetByDate(fidelityTxns).get(groupKey) ?? new Map();
  const data = buildGroupChartData(series, markers);
  const latest = series[series.length - 1];

  const sorted = fidelityTxnsToTickerTransactions(fidelityTxns, group.tickers)
    .sort((a, b) => b.runDate.localeCompare(a.runDate));

  const handleEnter = (h: HoverState) => setHover(h);
  const handleMove = (x: number, y: number) => setHover((prev) => (prev ? { ...prev, x, y } : null));
  const handleLeave = () => setHover(null);

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
      <div className="flex-1 min-h-0 px-4 pt-4 pb-2">
        <GroupChart
          data={data}
          onEnter={handleEnter}
          onMove={handleMove}
          onLeave={handleLeave}
          onSelect={setSelected}
          selectedKey={selected?.key ?? null}
          tooltipWrapperStyle={hover ? { visibility: "hidden" } : undefined}
        />
        {hover && <MarkerHoverPanel hover={hover} isDark={isDark} valueLabel="Value" />}
      </div>
      <TransactionTable
        transactions={sorted}
        selected={selected}
        tableScrollRef={tableScrollRef}
        isDark={isDark}
      />
    </ChartDialog>
  );
}
