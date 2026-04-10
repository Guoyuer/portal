"use client";

import { memo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  Brush,
  CartesianGrid,
  Cell,
  LabelList,
  Legend,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Props as LabelProps } from "recharts/types/component/Label";
import type { TooltipContentProps } from "recharts/types/component/Tooltip";
import type {
  CategoryData,
  MonthlyFlowPoint,
  SnapshotPoint,
} from "@/lib/schema";
import { fmtCurrencyShort, fmtMonth, fmtMonthYear } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke } from "@/lib/chart-styles";

const COLORS = ["#06b6d4", "#eab308", "#f59e0b", "#10b981", "#f87171"];

const CAT_COLOR_MAP: Record<string, string> = {
  "US Equity": "#0072B2",
  "Non-US Equity": "#009E73",
  "Crypto": "#E69F00",
  "Safe Net": "#56B4E9",
};

// ── Donut: Category Allocation ─────────────────────────────────────────────

export function AllocationDonut({
  categories,
  total,
}: {
  categories: CategoryData[];
  total: number;
}) {
  const data = categories.map((c) => ({ name: c.name, value: c.value, pct: c.pct }));

  return (
    <div className="flex flex-col items-center">
      <div className="relative" style={{ width: 240, height: 240 }}>
        <PieChart width={240} height={240}>
          <Pie
            data={data}
            cx="50%"
            cy="50%"
            innerRadius={65}
            outerRadius={105}
            dataKey="value"
            stroke="rgba(255,255,255,0.3)"
            strokeWidth={1}
          >
            {data.map((d, i) => (
              <Cell key={i} fill={CAT_COLOR_MAP[d.name] ?? COLORS[i % COLORS.length]} />
            ))}
          </Pie>
          <Tooltip
            formatter={(value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          />
        </PieChart>
        {/* Center label — positioned with CSS, not SVG <text> */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none drop-shadow-[0_1px_2px_rgba(0,0,0,0.3)]">
          <span className="text-xl font-bold text-foreground">{fmtCurrencyShort(total)}</span>
          <span className="text-xs text-muted-foreground">Total</span>
        </div>
      </div>
      {/* Legend — clean grid below */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-sm">
        {data.map((d, i) => (
          <div key={d.name} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: CAT_COLOR_MAP[d.name] ?? COLORS[i % COLORS.length] }} />
            <span className="text-muted-foreground">{d.name} {d.pct.toFixed(0)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Custom bar label: savings rate % above income bar ─────────────────────

function SavingsLabel(props: LabelProps) {
  const x = Number(props.x ?? 0);
  const y = Number(props.y ?? 0);
  const width = Number(props.width ?? 0);
  const value = Number(props.value ?? 0);
  if (!value) return null;
  return (
    <text
      x={x + width / 2}
      y={y - 6}
      textAnchor="middle"
      fontSize={9}
      fill={value > 0 ? "#22d3ee" : "#f87171"}
      fontWeight={500}
    >
      {Math.round(value)}%
    </text>
  );
}

// ── Custom tooltip: Income vs Expenses with savings rate ─────────────────

function FlowTooltip({ active, payload, label }: TooltipContentProps) {
  if (!active || !payload?.length) return null;
  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const style = tooltipStyle(isDark);
  const row = payload[0]?.payload as (MonthlyFlowPoint & { savings?: number }) | undefined;
  const fmt = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return (
    <div style={style}>
      <p style={{ fontWeight: 600, marginBottom: 4 }}>{fmtMonthYear(String(label))}</p>
      {row && <p style={{ color: isDark ? "#e5e7eb" : "#374151", margin: 0 }}>Income : {fmt(row.income)}</p>}
      {payload.map((entry, i) => (
        <p key={i} style={{ color: entry.color, margin: 0 }}>
          {String(entry.name)} : {fmt(Number(entry.value))}
        </p>
      ))}
      {row && row.savingsRate !== 0 && (
        <p style={{ color: row.savingsRate > 0 ? "#22d3ee" : "#f87171", margin: 0 }}>
          Savings Rate : {Math.round(row.savingsRate)}%
        </p>
      )}
    </div>
  );
}

// ── Stacked Bars: Expenses vs Savings ────────────────────────────────────

export const IncomeExpensesChart = memo(function IncomeExpensesChart({
  data,
  activeMonth,
}: {
  data: MonthlyFlowPoint[];
  activeMonth?: string; // e.g. "2026-03"
}) {
  const isMobile = useIsMobile();
  const isDark = useIsDark();
  const stacked = data
    .filter((d) => d.income > 0)
    .map((d) => ({ ...d, savings: Math.max(0, d.income - d.expenses) }));

  const activeIdx = activeMonth ? stacked.findIndex((d) => d.month === activeMonth) : -1;
  const expenseColor = isDark ? "#fb7185" : "#e94560";
  const savingsColor = isDark ? "#22d3ee" : "#0891b2";

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 280 : 360}>
      <BarChart
        data={stacked}
        barCategoryGap="20%"
        margin={{ top: 20, right: 10, left: isMobile ? -5 : 10, bottom: 0 }}
      >
        <CartesianGrid vertical={false} stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonth}
          fontSize={11}
          tick={{ fill: isDark ? "#9ca3af" : "#6b7280" }}
          axisLine={{ stroke: gridStroke(isDark) }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={fmtCurrencyShort}
          fontSize={11}
          tick={{ fill: isDark ? "#9ca3af" : "#6b7280" }}
          width={isMobile ? 38 : 50}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip cursor={false} content={FlowTooltip} />
        <Legend verticalAlign="top" height={28} />
        {/* Leader line from active bar to stat bar above */}
        {activeIdx >= 0 && (
          <ReferenceLine
            x={stacked[activeIdx].month}
            stroke={isDark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)"}
            strokeDasharray="2 3"
            strokeWidth={1}
          />
        )}
        <Bar dataKey="expenses" name="Expenses" stackId="income" fill={expenseColor} isAnimationActive={false}>
          {stacked.map((_, i) => (
            <Cell key={i} fill={expenseColor} opacity={activeIdx >= 0 && i !== activeIdx ? 0.35 : 0.9} />
          ))}
        </Bar>
        <Bar dataKey="savings" name="Savings" stackId="income" fill={savingsColor} radius={[2, 2, 0, 0]} isAnimationActive={false}>
          {stacked.map((_, i) => (
            <Cell key={i} fill={savingsColor} opacity={activeIdx >= 0 && i !== activeIdx ? 0.35 : 0.9} />
          ))}
          <LabelList dataKey="savingsRate" content={SavingsLabel} />
        </Bar>
        {stacked.length > 12 && (
          <Brush
            dataKey="month"
            height={24}
            stroke={isDark ? "#22d3ee" : "#0891b2"}
            fill={isDark ? "rgba(8,145,178,0.2)" : "rgba(207,250,254,0.5)"}
            tickFormatter={fmtMonth}
          />
        )}
      </BarChart>
    </ResponsiveContainer>
  );
});

// ── Area: Net Worth Trend ──────────────────────────────────────────────────

/** Round Y-axis domain to nice $50k boundaries */
function niceYDomain(data: SnapshotPoint[]): [number, number] {
  const vals = data.map((d) => d.total);
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  const step = 50_000;
  let lo = Math.floor(min / step) * step;
  let hi = Math.ceil(max / step) * step;
  if (lo === hi) { lo -= step / 2; hi += step / 2; }
  return [lo, hi];
}

export function NetWorthTrendChart({
  data,
}: {
  data: SnapshotPoint[];
}) {
  const isDark = useIsDark();
  const isMobile = useIsMobile();

  if (data.length === 0) return null;

  // Convert dates to timestamps for uniform time-axis spacing
  const chartData = data.map((d) => ({ ...d, ts: new Date(d.date).getTime() }));

  const [yMin, yMax] = niceYDomain(data);
  const nwEndIdx = chartData.length - 1;
  const nwStartIdx = Math.max(0, nwEndIdx - 11);
  const brushColor = isDark ? "#22d3ee" : "#0891b2";

  const fmtTick = (ts: number) =>
    new Date(ts).toLocaleDateString("en-US", { month: "short", year: "2-digit" });

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 260 : 300}>
      <AreaChart data={chartData} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="nwGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isDark ? "#22d3ee" : "#0891b2"} stopOpacity={0.35} />
            <stop offset="100%" stopColor={isDark ? "#22d3ee" : "#0891b2"} stopOpacity={0.02} />
          </linearGradient>
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
          domain={[yMin, yMax]}
          axisLine={false}
          tickLine={false}
        />
        <Tooltip
          contentStyle={tooltipStyle(isDark)}
          formatter={(value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          labelFormatter={(ts) => new Date(Number(ts)).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
        />
        <Area
          type="monotone"
          dataKey="total"
          stroke={isDark ? "#22d3ee" : "#0891b2"}
          fill="url(#nwGradient)"
          strokeWidth={2}
        />
        {chartData.length > 12 && (
          <Brush
            key={`nw-brush-${chartData.length}`}
            dataKey="ts"
            height={28}
            stroke={brushColor}
            fill={isDark ? "rgba(8,145,178,0.2)" : "rgba(207,250,254,0.5)"}
            startIndex={nwStartIdx}
            endIndex={nwEndIdx}
            tickFormatter={fmtTick}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
