"use client";

import {
  Area,
  AreaChart,
  Bar,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
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
              stroke="#fff"
              strokeWidth={2}
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
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
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

// ── Grouped Bars + Line: Income vs Expenses ────────────────────────────────

export function IncomeExpensesChart({
  data,
}: {
  data: MonthlyFlowPoint[];
}) {
  const isMobile = useIsMobile();
  const isDark = useIsDark();
  // Skip months with zero income (likely incomplete)
  const all = data.filter((d) => d.income > 0);
  const filtered = isMobile ? all.slice(-12) : all;

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 260 : 320}>
      <ComposedChart
        data={filtered}
        margin={{ top: 10, right: isMobile ? 5 : 40, left: isMobile ? -5 : 10, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonth}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          axisLine={{ stroke: gridStroke(isDark) }}
          tickLine={false}
        />
        <YAxis
          yAxisId="dollar"
          tickFormatter={fmtCurrencyShort}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          width={isMobile ? 38 : 50}
          axisLine={false}
          tickLine={false}
        />
        {!isMobile && (
          <YAxis
            yAxisId="pct"
            orientation="right"
            tickFormatter={(v: number) => `${v.toFixed(0)}%`}
            fontSize={11}
            tick={{ fill: "#2563eb" }}
            domain={[0, 100]}
            width={40}
            axisLine={false}
            tickLine={false}
          />
        )}
        <Tooltip
          contentStyle={tooltipStyle(isDark)}
          formatter={(value, name) => {
            const v = Number(value);
            if (name === "Savings %") return `${v.toFixed(1)}%`;
            return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
          }}
          labelFormatter={(label) => fmtMonthYear(String(label))}
        />
        <Legend />
        <Bar yAxisId="dollar" dataKey="income" name="Income" fill={isDark ? "#4ade80" : "#27ae60"} opacity={0.85} radius={[2, 2, 0, 0]} />
        <Bar yAxisId="dollar" dataKey="expenses" name="Expenses" fill={isDark ? "#f87171" : "#e94560"} opacity={0.85} radius={[2, 2, 0, 0]} />
        {!isMobile && (
          <Line
            yAxisId="pct"
            dataKey="savingsRate"
            name="Savings %"
            stroke="#2563eb"
            strokeWidth={2}
            strokeDasharray="4 3"
            dot={false}
          />
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

// ── Area: Net Worth Trend ──────────────────────────────────────────────────

export function NetWorthTrendChart({
  data,
}: {
  data: SnapshotPoint[];
}) {
  const isDark = useIsDark();

  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={250}>
      <AreaChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => {
            const dt = new Date(d);
            return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
          }}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          axisLine={{ stroke: gridStroke(isDark) }}
          tickLine={false}
        />
        <YAxis
          tickFormatter={fmtCurrencyShort}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          width={55}
          domain={["dataMin - 10000", "dataMax + 10000"]}
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
          fill={isDark ? "#1e3a5f" : "#dbeafe"}
          fillOpacity={0.5}
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
