"use client";

import { useId } from "react";
import type { ApiCategory, ApiTicker } from "@/lib/compute/computed-types";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format/format";
import { SAVINGS_RATE_GOOD, SAVINGS_RATE_WARNING } from "@/lib/format/thresholds";
import { CategorySummary } from "@/components/finance/category-summary";

// ── Savings Rate Card with radial progress ──────────────────────────────

const RING_SIZE = 64;
const RING_STROKE = 6;
const RING_R = (RING_SIZE - RING_STROKE) / 2;
const RING_C = 2 * Math.PI * RING_R;

// Tailwind-style semantic colors for the savings rate ring.
// The pre-tax arc reuses the same stroke color with strokeOpacity=0.3, so
// we never need to hand-compute rgba variants.
const MUTED_OPACITY = 0.3;

function savingsRateColor(rate: number): string {
  if (rate >= SAVINGS_RATE_GOOD) return "#059669";     // emerald-600
  if (rate >= SAVINGS_RATE_WARNING) return "#CA8A04";  // amber-600
  return "#DC2626";                                     // red-600
}

function SavingsRateCard({
  savingsRate,
  takehomeSavingsRate,
}: {
  savingsRate: number | null;
  takehomeSavingsRate: number | null;
}) {
  // Both values come from the same cashflow compute — always null together
  if (savingsRate == null || takehomeSavingsRate == null) {
    return (
      <div data-testid="savings-rate-card" className="liquid-glass p-4 flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <p className="text-xs sm:text-sm text-muted-foreground">Savings Rate</p>
          <p className="text-xl sm:text-2xl font-bold mt-1">N/A</p>
        </div>
      </div>
    );
  }

  const pretax = Math.max(0, savingsRate - takehomeSavingsRate);
  const takehomeArc = RING_C * (Math.min(takehomeSavingsRate, 100) / 100);
  const pretaxArc = RING_C * (Math.min(pretax, 100) / 100);
  const color = savingsRateColor(takehomeSavingsRate);

  return (
    <div data-testid="savings-rate-card" className="liquid-glass p-4 flex items-center gap-3">
      <div className="flex-1 min-w-0">
        <p className="text-xs sm:text-sm text-muted-foreground">Savings Rate</p>
        <p className="text-xl sm:text-2xl font-bold mt-1 tabular-nums" style={{ color }}>
          {Math.round(takehomeSavingsRate)}%
          <span className="text-[10px] font-normal text-muted-foreground ml-1">take-home</span>
        </p>
      </div>
      <svg data-testid="savings-rate-ring" width={RING_SIZE} height={RING_SIZE} className="flex-shrink-0 -rotate-90">
        {/* Background track */}
        <circle
          cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_R}
          fill="none" stroke="currentColor" strokeWidth={RING_STROKE}
          className="text-black/5 dark:text-white/10"
        />
        {/* Take-home arc */}
        <circle
          cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_R}
          fill="none" strokeWidth={RING_STROKE}
          strokeDasharray={`${takehomeArc} ${RING_C}`}
          strokeLinecap="round"
          stroke={color}
        />
        {/* Pre-tax arc (muted, starts after take-home) */}
        <circle
          cx={RING_SIZE / 2} cy={RING_SIZE / 2} r={RING_R}
          fill="none" strokeWidth={RING_STROKE}
          strokeDasharray={`${pretaxArc} ${RING_C}`}
          strokeDashoffset={-takehomeArc}
          strokeLinecap="round"
          stroke={color}
          strokeOpacity={MUTED_OPACITY}
        />
        <title>{`${Math.round(savingsRate)}% gross savings rate (${Math.round(takehomeSavingsRate)}% of take-home, ${Math.round(pretax)}% pre-tax)`}</title>
      </svg>
    </div>
  );
}

// ── MetricCards ──────────────────────────────────────────────────────────

export function MetricCards({
  allocation,
  savingsRate,
  takehomeSavingsRate,
  goal,
  allocationOpen,
  onAllocationToggle,
  colorByName,
}: {
  allocation: { total: number; netWorth: number; categories: ApiCategory[]; tickers: ApiTicker[] };
  savingsRate: number | null;
  takehomeSavingsRate: number | null;
  goal: number;
  allocationOpen: boolean;
  onAllocationToggle: () => void;
  colorByName: Record<string, string>;
}) {
  const { total, netWorth, categories, tickers } = allocation;
  const goalPct = (netWorth / goal) * 100;
  const safeNetValue = categories.find((c) => c.name === "Safe Net")?.value ?? 0;
  const investmentValue = total - safeNetValue;
  const invPct = netWorth > 0 ? (investmentValue / netWorth) * 100 : 0;
  const allocationContentId = useId();

  return (
    <div className="space-y-4">
      {/* Net Worth tile — expands to show allocation */}
      <div data-testid="net-worth-card" className="liquid-glass overflow-hidden">
        <button
          type="button"
          className="w-full p-4 text-left cursor-pointer"
          onClick={onAllocationToggle}
          aria-expanded={allocationOpen}
          aria-controls={allocationContentId}
          aria-label={allocationOpen ? "Hide allocation breakdown" : "Show allocation breakdown"}
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
          <div className="mt-2 flex justify-center items-center gap-1.5 text-xs text-muted-foreground">
            <span>{allocationOpen ? "Hide breakdown" : "Show breakdown"}</span>
            <svg
              xmlns="http://www.w3.org/2000/svg"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              className={`h-3.5 w-3.5 transition-transform duration-300 ${allocationOpen ? "rotate-180" : ""}`}
            >
              <path d="m6 9 6 6 6-6" />
            </svg>
          </div>
        </button>

        {/* Expandable allocation content — inside the same glass card */}
        <div
          id={allocationContentId}
          aria-hidden={!allocationOpen}
          inert={!allocationOpen}
          className="grid overflow-hidden transition-[grid-template-rows,opacity] duration-500 ease-[cubic-bezier(0.33,1,0.68,1)]"
          style={{
            gridTemplateRows: allocationOpen ? "1fr" : "0fr",
            opacity: allocationOpen ? 1 : 0,
          }}
        >
          <div className="min-h-0 overflow-hidden">
            <div className="mx-4 h-px bg-gradient-to-r from-transparent via-foreground/10 to-transparent" />
            <div className="p-4 pt-3">
              <CategorySummary categories={categories} tickers={tickers} total={total} title="Allocation" embedded colorByName={colorByName} />
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <SavingsRateCard savingsRate={savingsRate} takehomeSavingsRate={takehomeSavingsRate} />
        <div data-testid="goal-card" className="liquid-glass p-4 sm:col-span-2">
          <p className="text-xs sm:text-sm text-muted-foreground">Goal</p>
          <p className="text-xl sm:text-2xl font-bold mt-1">
            {Math.round(goalPct)}%{" "}
            <span className="text-xs font-normal text-muted-foreground">of ${Math.round(goal / 1_000_000)}M</span>
          </p>
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
}
