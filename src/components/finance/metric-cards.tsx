"use client";

import { memo, useRef, useState, useEffect } from "react";
import type { ApiCategory, ApiTicker } from "@/lib/types";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format";
import { savingsRateColor } from "@/lib/style-helpers";
import { CategorySummary } from "@/components/finance/category-summary";

export const MetricCards = memo(function MetricCards({
  total,
  netWorth,
  categories,
  tickers,
  savingsRate,
  takehomeSavingsRate,
  goal,
  goalPct,
  allocationOpen,
  onAllocationToggle,
}: {
  total: number;
  netWorth: number;
  categories: ApiCategory[];
  tickers: ApiTicker[];
  savingsRate: number | null;
  takehomeSavingsRate: number | null;
  goal: number;
  goalPct: number;
  allocationOpen: boolean;
  onAllocationToggle: () => void;
}) {
  const safeNetValue = categories.find((c) => c.name === "Safe Net")?.value ?? 0;
  const investmentValue = total - safeNetValue;
  const invPct = netWorth > 0 ? (investmentValue / netWorth) * 100 : 0;

  // Measure content height for smooth transition
  const contentRef = useRef<HTMLDivElement>(null);
  const [contentH, setContentH] = useState(0);

  useEffect(() => {
    if (!contentRef.current) return;
    const ro = new ResizeObserver(([entry]) => setContentH(entry.contentRect.height));
    ro.observe(contentRef.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="space-y-4">
      {/* Net Worth tile — expands to show allocation */}
      <div data-slot="card" className="liquid-glass overflow-hidden">
        <button
          type="button"
          className="w-full p-4 text-left cursor-pointer"
          onClick={onAllocationToggle}
        >
          <div className="flex items-baseline justify-between">
            <p className="text-sm text-muted-foreground">Net Worth</p>
            <p className="text-2xl font-bold tabular-nums">{fmtCurrency(netWorth)}</p>
          </div>
          <div className="mt-2 flex h-2 w-full rounded-full overflow-hidden">
            <div className="h-2 bg-cyan-400" style={{ width: `${100 - invPct}%` }} />
            <div className="h-2 bg-blue-500 flex-1" />
          </div>
          <div className="mt-2 flex justify-between text-xs">
            <div>
              <span className="inline-block w-2 h-2 rounded-sm bg-cyan-400 mr-1.5 align-middle" />
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

        {/* Expandable allocation content — inside the same glass card */}
        <div
          className="transition-[height,opacity] duration-500 ease-[cubic-bezier(0.33,1,0.68,1)]"
          style={{
            height: allocationOpen ? contentH : 0,
            opacity: allocationOpen ? 1 : 0,
          }}
        >
          <div ref={contentRef}>
            {allocationOpen && (
              <>
                <div className="mx-4 h-px bg-gradient-to-r from-transparent via-foreground/10 to-transparent" />
                <div className="p-4 pt-3">
                  <CategorySummary categories={categories} tickers={tickers} total={total} title="Allocation" embedded />
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-3 gap-4">
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-xs sm:text-sm text-muted-foreground">Savings Rate</p>
        {savingsRate != null ? (
          <p className={`text-xl sm:text-2xl font-bold mt-1 ${savingsRateColor(savingsRate)}`}>
            {Math.round(savingsRate)}%
          </p>
        ) : (
          <p className="text-xl sm:text-2xl font-bold mt-1">N/A</p>
        )}
      </div>
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-xs sm:text-sm text-muted-foreground">Take-home Rate</p>
        {takehomeSavingsRate != null ? (
          <p className={`text-xl sm:text-2xl font-bold mt-1 ${savingsRateColor(takehomeSavingsRate)}`}>
            {Math.round(takehomeSavingsRate)}%
          </p>
        ) : (
          <p className="text-xl sm:text-2xl font-bold mt-1">N/A</p>
        )}
      </div>
      <div data-slot="card" className="liquid-glass p-4">
        <p className="text-xs sm:text-sm text-muted-foreground">Goal</p>
        <p className="text-xl sm:text-2xl font-bold mt-1">{Math.round(goalPct)}% <span className="text-xs font-normal text-muted-foreground">of ${Math.round(goal / 1_000_000)}M</span></p>
        <div className="mt-2 h-2 w-full rounded-full bg-black/5 dark:bg-white/10">
          <div
            className="h-2 rounded-full bg-blue-500"
            style={{ width: `${Math.min(goalPct, 100)}%` }}
          />
        </div>
      </div>
      </div>
    </div>
  );
});
