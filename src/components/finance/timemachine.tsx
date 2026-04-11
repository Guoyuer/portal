"use client";

import { type ReactNode } from "react";
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
import type { DailyPoint, CashflowResponse, ActivityResponse } from "@/lib/schema";
import { fmtCurrency, fmtCurrencyShort, fmtDateLong, fmtDateMonthYear } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps, brushColors } from "@/lib/chart-styles";
import { CATEGORIES, CAT_COLOR_BY_KEY } from "@/lib/compute";

// ── Constants ─────────────────────────────────────────────────────────────

const CAT_LABELS: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c.key, c.name]),
);

const CAT_KEYS = ["safeNet", "crypto", "nonUsEquity", "usEquity"] as const;

// ── TimemachineChart ──────────────────────────────────────────────────────

export function TimemachineChart({
  daily,
  defaultStartIndex,
  defaultEndIndex,
  onBrushChange,
}: {
  daily: DailyPoint[];
  defaultStartIndex: number;
  defaultEndIndex: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();

  const chartData = daily.map((d) => ({ ...d, ts: new Date(d.date).getTime() }));

  if (daily.length === 0) return null;

  const fmtTick = (ts: number) =>
    new Date(ts).toLocaleDateString("en-US", { month: "short", year: "2-digit" });


  return (
    <ResponsiveContainer width="100%" height={isMobile ? 240 : 280}>
      <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          {CAT_KEYS.map((key) => (
            <linearGradient key={key} id={`tm-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CAT_COLOR_BY_KEY[key]} stopOpacity={0.85} />
              <stop offset="100%" stopColor={CAT_COLOR_BY_KEY[key]} stopOpacity={0.3} />
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
        <Tooltip
          contentStyle={tooltipStyle(isDark)}
          formatter={(value, name) => [fmtCurrency(Number(value)), CAT_LABELS[String(name)] ?? String(name)]}
          labelFormatter={(ts) =>
            new Date(Number(ts)).toLocaleDateString("en-US", {
              month: "long",
              day: "numeric",
              year: "numeric",
            })
          }
        />
        {CAT_KEYS.map((key) => (
          <Area
            key={key}
            type="monotone"
            dataKey={key}
            stackId="1"
            stroke="none"
            strokeWidth={0}
            fill={`url(#tm-${key})`}
            isAnimationActive={false}
          />
        ))}
        <Brush
          dataKey="ts"
          height={28}
          {...brushColors(isDark)}
          startIndex={defaultStartIndex}
          endIndex={defaultEndIndex}
          onChange={onBrushChange}
          tickFormatter={fmtTick}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}


// ── TimemachineSummary ──────────────────────────────────────────────────

export function TimemachineSummary({
  snapshot,
  cashflow,
  activity,
  startDate,
  crossCheck: cc,
}: {
  snapshot: DailyPoint | null;
  cashflow?: CashflowResponse | null;
  activity?: ActivityResponse | null;
  startDate?: string;
  crossCheck?: CrossCheck | null;
}) {
  if (!snapshot) return null;

  const total = snapshot.total;
  const catEntries = CAT_KEYS.map((key) => ({
    key,
    value: snapshot[key],
    pct: total > 0 ? (snapshot[key] / total) * 100 : 0,
  }));

  const rangeStats = (cashflow || activity)
    ? [
        { label: "Income", value: cashflow?.totalIncome ?? 0 },
        { label: "Expenses", value: cashflow?.totalExpenses ?? 0 },
        { label: "Buys", value: activity?.buysBySymbol.reduce((s, b) => s + b.total, 0) ?? 0 },
        { label: "Dividends", value: activity?.dividendsBySymbol.reduce((s, d) => s + d.total, 0) ?? 0 },
      ]
    : [];

  const rangeLabel = startDate
    ? `${fmtDateMonthYear(startDate)} — ${fmtDateMonthYear(snapshot.date)}`
    : "";

  return (
    <div className="space-y-3">
      {/* Date + total */}
      <div className="flex items-baseline justify-between">
        <p className="text-sm text-muted-foreground" data-testid="tm-date">{fmtDateLong(snapshot.date)}</p>
        <p className="text-xl font-bold tabular-nums" data-testid="tm-total">
          {fmtCurrency(total)}
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
            <p className="text-muted-foreground">{CAT_LABELS[key]}</p>
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
          <div className="grid grid-cols-4 gap-2 text-xs">
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
          <div className="grid grid-cols-3 gap-2 text-xs">
            <div>
              <p className="text-muted-foreground">Fidelity Total</p>
              <p className="font-semibold tabular-nums mt-0.5">{fmtCurrencyShort(cc.fidelityTotal)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Matched</p>
              <p className="font-semibold tabular-nums mt-0.5 text-green-500">{fmtCurrencyShort(cc.matchedTotal)}</p>
            </div>
            <div>
              <p className="text-muted-foreground">Unmatched</p>
              <p className={`font-semibold tabular-nums mt-0.5 ${cc.unmatchedTotal > 0 ? "text-red-400" : ""}`}>
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
    <section id="timemachine">
      <div className="liquid-glass p-4 sm:p-5">
        <TimemachineSummary
          snapshot={tl.snapshot}
          cashflow={tl.cashflow}
          activity={tl.activity}
          startDate={tl.startDate ?? undefined}
          crossCheck={tl.crossCheck}
        />
        <div className="mt-4">
          <TimemachineChart
            daily={tl.chartDaily}
            defaultStartIndex={tl.defaultStartIndex}
            defaultEndIndex={tl.defaultEndIndex}
            onBrushChange={tl.onBrushChange}
          />
        </div>
      </div>
    </section>
  );
}
