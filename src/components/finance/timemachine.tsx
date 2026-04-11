"use client";

import { type ReactNode, useState } from "react";
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
import type { DailyPoint, CashflowResponse, ActivityResponse } from "@/lib/schema";
import { fmtCurrency, fmtCurrencyShort, fmtDateLong, fmtDateMedium, fmtDateMonthYear } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke, axisProps, brushColors } from "@/lib/chart-styles";
import { CATEGORIES, CAT_COLOR_BY_KEY } from "@/lib/compute";

// ── Constants ─────────────────────────────────────────────────────────────

const CAT_LABELS: Record<string, string> = Object.fromEntries(
  CATEGORIES.map((c) => [c.key, c.name]),
);

function AreaTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const style = tooltipStyle(isDark);
  const fmtLabel = new Date(Number(label)).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
  return (
    <div style={style}>
      <p style={{ fontWeight: 600, marginBottom: 2 }}>{fmtLabel}</p>
      {payload.map((entry, i) => (
        <p key={i} style={{ color: entry.color, margin: 0 }}>
          {CAT_LABELS[String(entry.name)] ?? String(entry.name)} : {fmtCurrency(Number(entry.value))}
        </p>
      ))}
    </div>
  );
}

const CAT_KEYS = ["safeNet", "crypto", "nonUsEquity", "usEquity"] as const;

// ── TimemachineChart ──────────────────────────────────────────────────────

export function TimemachineChart({
  daily,
  brushStart,
  brushEnd,
}: {
  daily: DailyPoint[];
  brushStart: number;
  brushEnd: number;
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();

  // Slice to brush range so chart zooms with the brush
  const sliced = daily.slice(brushStart, brushEnd + 1);
  const chartData = sliced.map((d) => ({ ...d, ts: new Date(d.date).getTime() }));

  if (daily.length === 0) return null;

  const fmtTick = (ts: number) =>
    new Date(ts).toLocaleDateString("en-US", { month: "short", year: "2-digit" });

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 240 : 280}>
      <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          {CAT_KEYS.map((key) => (
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
        <Tooltip content={AreaTooltip} />
        {CAT_KEYS.map((key) => (
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

export function StickyBrush({
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
  const [range, setRange] = useState({ start: defaultStartIndex, end: defaultEndIndex });
  const chartData = daily.map((d) => ({ ...d, ts: new Date(d.date).getTime() }));
  if (daily.length === 0) return null;

  const startLabel = fmtDateMedium(daily[range.start]?.date ?? daily[0].date);
  const endLabel = fmtDateMedium(daily[range.end]?.date ?? daily[daily.length - 1].date);

  const handleChange = (state: { startIndex?: number; endIndex?: number }) => {
    setRange({ start: state.startIndex ?? range.start, end: state.endIndex ?? range.end });
    onBrushChange(state);
  };

  return (
    <div className="fixed bottom-0 left-0 right-0 md:left-56 z-40 bg-background/80 backdrop-blur-md border-t border-border px-4 py-2">
      <div className="max-w-5xl mx-auto flex items-center gap-3">
        <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap w-[90px] text-right">{startLabel}</span>
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
                onChange={handleChange}
                tickFormatter={() => ""}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
        <span className="text-xs text-muted-foreground tabular-nums whitespace-nowrap w-[90px]">{endLabel}</span>
      </div>
    </div>
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
  const netWorth = total + snapshot.liabilities;
  const catEntries = CAT_KEYS.map((key) => ({
    key,
    value: snapshot[key],
    pct: total > 0 ? (snapshot[key] / total) * 100 : 0,
  }));

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
    ? `${fmtDateMedium(startDate)} — ${fmtDateMedium(snapshot.date)}`
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
            brushStart={tl.brushStart}
            brushEnd={tl.brushEnd}
          />
        </div>
      </div>
    </section>
  );
}
