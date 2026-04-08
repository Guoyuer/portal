"use client";

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
import type { DailyPoint, PrefixPoint } from "@/lib/schema";
import { fmtCurrency, fmtCurrencyShort } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke } from "@/lib/chart-styles";

// ── Constants ─────────────────────────────────────────────────────────────

const CAT_COLORS = {
  usEquity: "#3b82f6",
  nonUsEquity: "#8b5cf6",
  crypto: "#f59e0b",
  safeNet: "#06b6d4",
} as const;

const CAT_LABELS: Record<string, string> = {
  usEquity: "US Equity",
  nonUsEquity: "Non-US Equity",
  crypto: "Crypto",
  safeNet: "Safe Net",
};

const CAT_KEYS = ["safeNet", "crypto", "nonUsEquity", "usEquity"] as const;

// ── TimemachineChart ──────────────────────────────────────────────────────

export function TimemachineChart({
  daily,
  startIndex,
  endIndex,
  onBrushChange,
}: {
  daily: DailyPoint[];
  startIndex: number;
  endIndex: number;
  onBrushChange: (state: { startIndex?: number; endIndex?: number }) => void;
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();

  if (daily.length === 0) return null;

  const chartData = daily.map((d) => ({ ...d, ts: new Date(d.date).getTime() }));

  const fmtTick = (ts: number) =>
    new Date(ts).toLocaleDateString("en-US", { month: "short", year: "2-digit" });

  const brushColor = isDark ? "#22d3ee" : "#0891b2";

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 240 : 280}>
      <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          {CAT_KEYS.map((key) => (
            <linearGradient key={key} id={`tm-${key}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={CAT_COLORS[key]} stopOpacity={0.6} />
              <stop offset="100%" stopColor={CAT_COLORS[key]} stopOpacity={0.05} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="ts"
          type="number"
          scale="time"
          domain={["dataMin", "dataMax"]}
          tickFormatter={fmtTick}
          fontSize={11}
          tick={{ fill: isDark ? "#9ca3af" : "#6b7280" }}
          axisLine={{ stroke: gridStroke(isDark) }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={fmtCurrencyShort}
          fontSize={11}
          tick={{ fill: isDark ? "#9ca3af" : "#6b7280" }}
          width={55}
          axisLine={false}
          tickLine={false}
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
          />
        ))}
        <Brush
          dataKey="ts"
          height={28}
          stroke={brushColor}
          fill={isDark ? "rgba(8,145,178,0.2)" : "rgba(207,250,254,0.5)"}
          startIndex={startIndex}
          endIndex={endIndex}
          onChange={onBrushChange}
          tickFormatter={fmtTick}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

// ── TimemachineSummary ────────────────────────────────────────────────────

// ── Date formatting helpers ──────────────────────────────────────────────

function fmtDate(iso: string): string {
  const [y, m, d] = iso.split("-");
  const dt = new Date(+y, +m - 1, +d);
  return dt.toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" });
}

function fmtDateShort(iso: string): string {
  const [y, m] = iso.split("-");
  const dt = new Date(+y, +m - 1, 1);
  return dt.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

// ── TimemachineSummary ──────────────────────────────────────────────────

export function TimemachineSummary({
  snapshot,
  range,
  startDate,
}: {
  snapshot: DailyPoint | null;
  range: PrefixPoint | null;
  startDate?: string;
}) {
  if (!snapshot) return null;

  const total = snapshot.total;
  const catEntries = CAT_KEYS.map((key) => ({
    key,
    value: snapshot[key],
    pct: total > 0 ? (snapshot[key] / total) * 100 : 0,
  }));

  const rangeStats = range
    ? [
        { label: "Income", value: range.income },
        { label: "Expenses", value: range.expenses },
        { label: "Buys", value: range.buys },
        { label: "Dividends", value: range.dividends },
      ]
    : [];

  const rangeLabel = startDate
    ? `${fmtDateShort(startDate)} — ${fmtDateShort(snapshot.date)}`
    : "";

  return (
    <div className="space-y-3">
      {/* Date + total */}
      <div className="flex items-baseline justify-between">
        <p className="text-sm text-muted-foreground" data-testid="tm-date">{fmtDate(snapshot.date)}</p>
        <p className="text-xl font-bold tabular-nums transition-all duration-150" data-testid="tm-total">
          {fmtCurrency(total)}
        </p>
      </div>

      {/* Allocation bar */}
      <div className="flex h-2 w-full rounded-full overflow-hidden">
        {catEntries.map(({ key, pct }) => (
          <div
            key={key}
            className="h-2 transition-all duration-150"
            style={{ width: `${pct}%`, backgroundColor: CAT_COLORS[key] }}
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
                style={{ backgroundColor: CAT_COLORS[key] }}
              />
              <span className="text-muted-foreground transition-all duration-150">
                {pct.toFixed(0)}%
              </span>
            </div>
            <p className="font-semibold tabular-nums mt-0.5 transition-all duration-150">
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
                <p className="font-semibold tabular-nums mt-0.5 transition-all duration-150">
                  {fmtCurrencyShort(value)}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
