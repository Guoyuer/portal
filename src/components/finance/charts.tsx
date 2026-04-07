"use client";

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
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
// eslint-disable-next-line @typescript-eslint/no-explicit-any
type FlowTooltipProps = { active?: boolean; payload?: any[]; label?: string };
import type {
  CategoryData,
  MonthlyFlowPoint,
  SnapshotPoint,
} from "@/lib/types";
import { fmtCurrencyShort, fmtMonth, fmtMonthYear } from "@/lib/format";
import { useIsDark, useIsMobile } from "@/lib/hooks";
import { tooltipStyle, gridStroke } from "@/lib/chart-styles";

const COLORS = ["#2563eb", "#7c3aed", "#f59e0b", "#10b981", "#ef4444"];

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
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
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
              {data.map((_, i) => (
                <Cell key={i} fill={COLORS[i % COLORS.length]} />
              ))}
            </Pie>
            <Tooltip
              formatter={(value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
            />
          </PieChart>
        </ResponsiveContainer>
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
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: COLORS[i % COLORS.length] }} />
            <span className="text-muted-foreground">{d.name} {d.pct.toFixed(0)}%</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Custom bar label: savings rate % above income bar ─────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function SavingsLabel(props: any) {
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
      fill={value > 0 ? "#2563eb" : "#ef4444"}
      fontWeight={500}
    >
      {Math.round(value)}%
    </text>
  );
}

// ── Custom tooltip: Income vs Expenses with savings rate ─────────────────

function FlowTooltip({ active, payload, label }: FlowTooltipProps) {
  if (!active || !payload?.length) return null;
  const isDark = typeof document !== "undefined" && document.documentElement.classList.contains("dark");
  const style = tooltipStyle(isDark);
  const row = payload[0]?.payload as MonthlyFlowPoint | undefined;
  const fmt = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return (
    <div style={style}>
      <p style={{ fontWeight: 600, marginBottom: 4 }}>{fmtMonthYear(String(label))}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey as string} style={{ color: entry.color, margin: 0 }}>
          {entry.name} : {fmt(Number(entry.value))}
        </p>
      ))}
      {row && row.savingsRate !== 0 && (
        <p style={{ color: row.savingsRate > 0 ? "#2563eb" : "#ef4444", margin: 0 }}>
          Savings Rate : {Math.round(row.savingsRate)}%
        </p>
      )}
    </div>
  );
}

// ── Grouped Bars: Income vs Expenses ──────────────────────────────────────

export function IncomeExpensesChart({
  data,
}: {
  data: MonthlyFlowPoint[];
}) {
  const isMobile = useIsMobile();
  const isDark = useIsDark();
  const filtered = data.filter((d) => d.income > 0);

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 280 : 360}>
      <BarChart
        data={filtered}
        barGap={2}
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
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <Tooltip cursor={false} content={FlowTooltip as any} />
        <Legend verticalAlign="top" height={28} />
        <Bar dataKey="income" name="Income" fill={isDark ? "#4ade80" : "#27ae60"} opacity={0.85} radius={[2, 2, 0, 0]}>
          <LabelList dataKey="savingsRate" content={SavingsLabel} />
        </Bar>
        <Bar dataKey="expenses" name="Expenses" fill={isDark ? "#f87171" : "#e94560"} opacity={0.85} radius={[2, 2, 0, 0]} />
        {filtered.length > 12 && (
          <Brush
            dataKey="month"
            height={24}
            stroke={isDark ? "#4ade80" : "#27ae60"}
            fill={isDark ? "rgba(30,95,58,0.3)" : "rgba(220,252,231,0.5)"}
            tickFormatter={fmtMonth}
          />
        )}
      </BarChart>
    </ResponsiveContainer>
  );
}

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

  const [yMin, yMax] = niceYDomain(data);
  const nwEndIdx = data.length - 1;
  const nwStartIdx = Math.max(0, nwEndIdx - 11);
  const brushColor = isDark ? "#60a5fa" : "#2563eb";

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 260 : 300}>
      <AreaChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <defs>
          <linearGradient id="nwGradient" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={isDark ? "#60a5fa" : "#2563eb"} stopOpacity={0.35} />
            <stop offset="100%" stopColor={isDark ? "#60a5fa" : "#2563eb"} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => {
            const dt = new Date(d);
            return dt.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
          }}
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
          labelFormatter={(label) => new Date(String(label)).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
        />
        <Area
          type="monotone"
          dataKey="total"
          stroke={isDark ? "#60a5fa" : "#2563eb"}
          fill="url(#nwGradient)"
          strokeWidth={2}
        />
        {data.length > 12 && (
          <Brush
            key={`nw-brush-${data.length}`}
            dataKey="date"
            height={28}
            stroke={brushColor}
            fill={isDark ? "rgba(30,58,95,0.3)" : "rgba(219,234,254,0.5)"}
            startIndex={nwStartIdx}
            endIndex={nwEndIdx}
            tickFormatter={(d: string) => {
              const dt = new Date(d);
              return dt.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
            }}
          />
        )}
      </AreaChart>
    </ResponsiveContainer>
  );
}
