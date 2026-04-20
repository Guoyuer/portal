"use client";

// ── Group chart dialog: mirrors TickerChartDialog for equivalent-ticker groups ──

import { useEffect, useRef } from "react";
import { GroupChart, buildGroupChartData } from "./group-chart";
import { buildGroupValueSeries, groupNetByDate } from "@/lib/format/group-aggregation";
import { EQUIVALENT_GROUPS } from "@/lib/config/equivalent-groups";
import { useIsDark } from "@/lib/hooks/hooks";
import { fmtCurrency, fmtPct } from "@/lib/format/format";
import { valueColor } from "@/lib/format/thresholds";
import type { DailyTicker, FidelityTxn } from "@/lib/schemas";

export function GroupChartDialog({
  groupKey,
  dailyTickers,
  fidelityTxns,
  startDate,
  endDate,
  onClose,
}: {
  groupKey: string;
  dailyTickers: DailyTicker[];
  fidelityTxns: FidelityTxn[];
  startDate?: string;
  endDate?: string;
  onClose: () => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const isDark = useIsDark();
  const group = EQUIVALENT_GROUPS[groupKey];

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    el.showModal();
    const onCancel = (e: Event) => { e.preventDefault(); onClose(); };
    el.addEventListener("cancel", onCancel);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      el.removeEventListener("cancel", onCancel);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  if (!group) return null;

  const series = buildGroupValueSeries(dailyTickers, group.tickers)
    .filter((p) => (!startDate || p.date >= startDate) && (!endDate || p.date <= endDate));
  const markers = groupNetByDate(fidelityTxns).get(groupKey) ?? new Map();
  const data = buildGroupChartData(series, markers);
  const latest = series[series.length - 1];

  return (
    <dialog
      ref={dialogRef}
      onClick={(e) => {
        e.stopPropagation();
        if (e.target === dialogRef.current) onClose();
      }}
      className="fixed inset-0 m-auto backdrop:bg-black/50 backdrop:backdrop-blur-sm bg-transparent p-0 max-w-none max-h-none border-0 overflow-visible"
    >
      <div className={`${isDark ? "bg-zinc-900 text-zinc-100" : "bg-white text-zinc-900"} rounded-xl shadow-2xl flex flex-col resize overflow-hidden w-[95vw] h-[92vh] min-w-[400px] min-h-[300px] max-w-[99vw] max-h-[98vh]`}>
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-foreground/10">
          <div className="flex items-baseline gap-3 min-w-0 flex-wrap">
            <span className="font-semibold text-lg truncate">{group.display}</span>
            {latest && (() => {
              const gainLoss = latest.value - latest.costBasis;
              const gainLossPct = latest.costBasis > 0 ? (gainLoss / latest.costBasis) * 100 : 0;
              const signedCurrency = `${gainLoss >= 0 ? "+" : ""}${fmtCurrency(gainLoss)}`;
              return (
                <>
                  <span className="text-sm text-muted-foreground">value {fmtCurrency(latest.value)}</span>
                  <span className="text-sm text-muted-foreground">cost {fmtCurrency(latest.costBasis)}</span>
                  <span className={`text-sm ${valueColor(gainLoss)}`}>
                    {signedCurrency} ({fmtPct(gainLossPct, true)})
                  </span>
                </>
              );
            })()}
            {latest && latest.value > 0 ? (
              <span className="text-xs text-muted-foreground truncate">
                {latest.constituents
                  .slice()
                  .sort((a, b) => b.value - a.value)
                  .map((c) => `${c.ticker} ${fmtPct((c.value / latest.value) * 100, false)}`)
                  .join(" · ")}
              </span>
            ) : (
              <span className="text-xs text-muted-foreground truncate">{group.tickers.join(" · ")}</span>
            )}
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className={`w-8 h-8 flex items-center justify-center rounded-full text-2xl leading-none ${isDark ? "hover:bg-zinc-800 text-zinc-300 hover:text-zinc-50" : "hover:bg-zinc-100 text-zinc-500 hover:text-zinc-900"} transition-colors`}
          >
            &times;
          </button>
        </div>
        <div className="flex-1 min-h-0 px-4 pt-4 pb-4">
          <GroupChart data={data} />
        </div>
      </div>
    </dialog>
  );
}
