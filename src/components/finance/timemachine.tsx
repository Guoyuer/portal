"use client";

import { useEffect, useState, type ReactNode } from "react";
import type { BundleState, CrossCheck } from "@/lib/use-bundle";
import {
  Area,
  AreaChart,
  Brush,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import type { CategoryMeta, DailyPoint } from "@/lib/schemas";
import type { CashflowResponse, ActivityResponse } from "@/lib/computed-types";
import { fmtCurrency, fmtCurrencyShort, fmtDateLong, fmtDateMedium, fmtDateMonthYear, fmtTick, parseLocalDate } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { gridStroke, axisProps, brushColors } from "@/lib/chart-styles";
import { TooltipCard } from "@/components/charts/tooltip-card";
import { CAT_COLOR_BY_KEY } from "@/lib/chart-colors";

// ── Constants ─────────────────────────────────────────────────────────────

/** Area stacking order in the chart (bottom → top). */
const CAT_STACK_ORDER: readonly string[] = ["safeNet", "crypto", "nonUsEquity", "usEquity"];

function stackKeys(categories: CategoryMeta[]): string[] {
  const present = new Set(categories.map((c) => c.key));
  return CAT_STACK_ORDER.filter((k) => present.has(k));
}

function catLabelsByKey(categories: CategoryMeta[]): Record<string, string> {
  return Object.fromEntries(categories.map((c) => [c.key, c.name]));
}

function AreaTooltip({
  active,
  payload,
  label,
  labels,
}: TooltipContentProps & { labels: Record<string, string> }) {
  const fmtLabel = new Date(Number(label)).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  return (
    <TooltipCard active={active} payload={payload} title={fmtLabel}>
      {payload && payload.length > 0 && (
        <p style={{ margin: 0, fontWeight: 600 }}>
          Total: {fmtCurrency(payload.reduce((s, e) => s + Number(e.value ?? 0), 0))}
        </p>
      )}
      {payload?.map((entry, i) => (
        <p key={i} style={{ color: entry.color, margin: 0 }}>
          {labels[String(entry.name)] ?? String(entry.name)}: {fmtCurrency(Number(entry.value))}
        </p>
      ))}
    </TooltipCard>
  );
}

// ── TimemachineChart ──────────────────────────────────────────────────────

function TimemachineChart({
  daily,
  brushStart,
  brushEnd,
  categories,
}: {
  daily: DailyPoint[];
  brushStart: number;
  brushEnd: number;
  categories: CategoryMeta[];
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();

  // Slice to brush range so chart zooms with the brush
  const sliced = daily.slice(brushStart, brushEnd + 1);
  const chartData = sliced.map((d) => ({ ...d, ts: parseLocalDate(d.date).getTime() }));
  const keys = stackKeys(categories);
  const labels = catLabelsByKey(categories);

  if (daily.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 240 : 280}>
      <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          {keys.map((key) => (
            <linearGradient key={key} id={`tmGrad-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CAT_COLOR_BY_KEY[key]} stopOpacity={0.9} />
              <stop offset="100%" stopColor={CAT_COLOR_BY_KEY[key]} stopOpacity={0.4} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} vertical={false} />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={fmtTick}
          {...axisProps(isDark)}
        />
        <YAxis
          tickFormatter={fmtCurrencyShort}
          width={55}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip content={(props) => <AreaTooltip {...props} labels={labels} />} />
        {keys.map((key) => (
          <Area
            key={key}
            type="monotone"
            dataKey={key}
            stackId="1"
            stroke="none"
            strokeWidth={0}
            fill={`url(#tmGrad-${key})`}
            fillOpacity={1}
            isAnimationActive={false}
          />
        ))}
      </AreaChart>
    </ResponsiveContainer>
  );
}


// ── StickyBrush ──────────────────────────────────────────────────────────

const BRUSH_COLLAPSED_KEY = "stickyBrush:collapsed";

function useBrushCollapsed(): [boolean, (next: boolean) => void] {
  const [collapsed, setCollapsed] = useState<boolean>(false);
  const isMobile = useIsMobile();

  // Load persisted pref after mount (mobile only).
  useEffect(() => {
    if (!isMobile) return;
    try {
      const stored = window.localStorage.getItem(BRUSH_COLLAPSED_KEY);
      if (stored === "1") setCollapsed(true);
    } catch { /* ignore storage errors */ }
  }, [isMobile]);

  const setAndPersist = (next: boolean) => {
    setCollapsed(next);
    try {
      window.localStorage.setItem(BRUSH_COLLAPSED_KEY, next ? "1" : "0");
    } catch { /* ignore storage errors */ }
  };

  return [isMobile && collapsed, setAndPersist];
}

export function StickyBrush({
  daily,
  defaultStartIndex,
  defaultEndIndex,
  brushStart,
  brushEnd,
  onBrushChange,
}: {
  daily: DailyPoint[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  brushStart: number;
  brushEnd: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
}) {
  const isDark = useIsDark();
  const [collapsed, setCollapsed] = useBrushCollapsed();
  const chartData = daily.map((d) => ({ ...d, ts: parseLocalDate(d.date).getTime() }));
  if (daily.length === 0) return null;

  const startLabel = fmtDateMedium(daily[brushStart].date);
  const endLabel = fmtDateMedium(daily[brushEnd].date);

  return (
    <div className="fixed bottom-0 left-0 right-0 md:left-56 z-40 bg-background/80 backdrop-blur-md border-t border-border px-4 py-2">
      <div className="max-w-5xl mx-auto">
        {/* Mobile collapsed pill */}
        {collapsed ? (
          <button
            type="button"
            onClick={() => setCollapsed(false)}
            aria-expanded={false}
            aria-label="Expand timeline brush"
            className="flex w-full items-center justify-between gap-2 py-1 text-xs text-muted-foreground tabular-nums"
          >
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden className="inline-block h-1 w-6 rounded-full bg-foreground/20" />
              {startLabel} — {endLabel}
            </span>
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
              <path d="m18 15-6-6-6 6" />
            </svg>
          </button>
        ) : (
          <>
            <div className="flex items-center justify-between text-xs text-muted-foreground tabular-nums mb-1 md:hidden">
              <span>{startLabel}</span>
              <span>{endLabel}</span>
              <button
                type="button"
                onClick={() => setCollapsed(true)}
                aria-expanded={true}
                aria-label="Collapse timeline brush"
                className="p-1 -m-1"
              >
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" className="h-3.5 w-3.5">
                  <path d="m6 9 6 6 6-6" />
                </svg>
              </button>
            </div>
            <div className="flex items-center gap-3">
              <span className="hidden md:inline text-xs text-muted-foreground tabular-nums whitespace-nowrap w-[90px] text-right">{startLabel}</span>
              <div className="flex-1 min-w-0">
                <ResponsiveContainer width="100%" height={34}>
                  <AreaChart data={chartData} margin={{ top: 0, right: 0, left: 0, bottom: 0 }}>
                    <XAxis dataKey="ts" hide />
                    <YAxis hide />
                    <Area type="monotone" dataKey="total" stroke="none" fill={isDark ? "rgba(255,255,255,0.06)" : "rgba(0,0,0,0.04)"} isAnimationActive={false} />
                    <Brush
                      dataKey="ts"
                      height={28}
                      {...brushColors(isDark)}
                      startIndex={defaultStartIndex}
                      endIndex={defaultEndIndex}
                      onChange={onBrushChange}
                      tickFormatter={() => ""}
                    />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <span className="hidden md:inline text-xs text-muted-foreground tabular-nums whitespace-nowrap w-[90px]">{endLabel}</span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

// ── TimemachineSummary ──────────────────────────────────────────────────

export function TimemachineSummary({
  snapshot,
  categories,
  cashflow,
  activity,
  startDate,
  crossCheck: cc,
}: {
  snapshot: DailyPoint | null;
  categories: CategoryMeta[];
  cashflow?: CashflowResponse | null;
  activity?: ActivityResponse | null;
  startDate?: string;
  crossCheck?: CrossCheck | null;
}) {
  if (!snapshot) return null;

  const total = snapshot.total;
  const netWorth = total + snapshot.liabilities;
  const keys = stackKeys(categories);
  const labels = catLabelsByKey(categories);
  const catEntries = keys.map((key) => {
    const value = (snapshot[key as keyof DailyPoint] as number | undefined) ?? 0;
    return {
      key,
      value,
      pct: total > 0 ? (value / total) * 100 : 0,
    };
  });

  const rangeStats = (cashflow || activity)
    ? [
        { label: "Net Savings", value: cashflow?.netCashflow ?? 0 },
        { label: "Investments", value: activity?.buysBySymbol.reduce((s, b) => s + b.total, 0) ?? 0 },
        { label: "CC Payments", value: cashflow?.ccPayments ?? 0 },
        { label: "Income", value: cashflow?.totalIncome ?? 0 },
        { label: "Expenses", value: cashflow?.totalExpenses ?? 0 },
        { label: "Dividends", value: activity?.dividendsBySymbol.reduce((s, d) => s + d.total, 0) ?? 0 },
      ]
    : [];

  const rangeLabel = startDate
    ? `Over ${fmtDateMedium(startDate)} — ${fmtDateMedium(snapshot.date)}`
    : "";

  return (
    <div className="space-y-3">
      {/* Date + total */}
      <div className="flex items-baseline justify-between">
        <p className="text-sm text-muted-foreground" data-testid="tm-date">{fmtDateLong(snapshot.date)}</p>
        <p className="text-xl font-bold tabular-nums" data-testid="tm-total">
          {fmtCurrency(netWorth)}
        </p>
      </div>

      {/* Allocation bar */}
      <div className="flex h-2 w-full rounded-full overflow-hidden">
        {catEntries.map(({ key, pct }) => (
          <div
            key={key}
            className="h-2"
            style={{ width: `${pct}%`, backgroundColor: CAT_COLOR_BY_KEY[key] }}
          />
        ))}
      </div>

      {/* 4 category stats */}
      <div className="grid grid-cols-4 gap-2 text-xs">
        {catEntries.map(({ key, value, pct }) => (
          <div key={key}>
            <div className="flex items-center gap-1">
              <span
                className="inline-block w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: CAT_COLOR_BY_KEY[key] }}
              />
              <span className="text-muted-foreground">
                {pct.toFixed(0)}%
              </span>
            </div>
            <p className="font-semibold tabular-nums mt-0.5">
              {fmtCurrencyShort(value)}
            </p>
            <p className="text-muted-foreground">{labels[key]}</p>
          </div>
        ))}
      </div>

      {/* Range stats */}
      {rangeStats.length > 0 && (
        <>
          <div className="border-t border-border" />
          <div className="flex items-center justify-between text-xs mb-1">
            <p className="text-muted-foreground font-medium">{rangeLabel}</p>
          </div>
          <div className="grid grid-cols-3 gap-2 text-xs">
            {rangeStats.map(({ label, value }) => (
              <div key={label}>
                <p className="text-muted-foreground">{label}</p>
                <p className="font-semibold tabular-nums mt-0.5">
                  {fmtCurrencyShort(value)}
                </p>
              </div>
            ))}
          </div>
        </>
      )}

      {/* Cross-check: Fidelity deposits vs Qianji transfers */}
      {cc && cc.totalCount > 0 && (
        <>
          <div className="border-t border-border" />
          <div className="flex items-center justify-between text-xs mb-1">
            <p className="text-muted-foreground font-medium">
              Deposit Cross-check
              <span className={`ml-1.5 ${cc.ok ? "text-green-500" : "text-red-400"}`}>
                {cc.ok ? "\u2713" : "\u2717"} {cc.matchedCount}/{cc.totalCount}
              </span>
            </p>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-y-1.5 gap-x-2 text-xs">
            <div className="flex sm:block justify-between">
              <p className="text-muted-foreground">Fidelity Total</p>
              <p className="font-semibold tabular-nums sm:mt-0.5">{fmtCurrencyShort(cc.fidelityTotal)}</p>
            </div>
            <div className="flex sm:block justify-between">
              <p className="text-muted-foreground">Matched</p>
              <p className="font-semibold tabular-nums sm:mt-0.5 text-green-500">{fmtCurrencyShort(cc.matchedTotal)}</p>
            </div>
            <div className="flex sm:block justify-between">
              <p className="text-muted-foreground">Unmatched</p>
              <p className={`font-semibold tabular-nums sm:mt-0.5 ${cc.unmatchedTotal > 0 ? "text-red-400" : ""}`}>
                {fmtCurrencyShort(cc.unmatchedTotal)}
              </p>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

// ── TimemachineSection ──────────────────────────────────────────────────

export function TimemachineSection({
  timeline: tl,
  fallback,
}: {
  timeline: BundleState;
  fallback: ReactNode;
}) {
  if (tl.loading || tl.error || tl.chartDaily.length === 0) {
    return <div id="net-worth">{fallback}</div>;
  }

  return (
    <div id="timemachine" className="scroll-mt-20 md:scroll-mt-8">
      <div className="liquid-glass p-4 sm:p-5">
        <TimemachineSummary
          snapshot={tl.snapshot}
          categories={tl.categories}
          cashflow={tl.cashflow}
          activity={tl.activity}
          startDate={tl.startDate ?? undefined}
          crossCheck={tl.crossCheck}
        />
        <div className="mt-4">
          <TimemachineChart
            daily={tl.chartDaily}
            brushStart={tl.brushStart}
            brushEnd={tl.brushEnd}
            categories={tl.categories}
          />
        </div>
      </div>
    </div>
  );
}
