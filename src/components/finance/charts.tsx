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
  ChartData,
  MonthlyFlowPoint,
  SnapshotPoint,
} from "@/lib/types";

const COLORS = ["#2563eb", "#7c3aed", "#f59e0b", "#10b981", "#ef4444"];

function fmtK(v: number) {
  if (v >= 1000) return `$${(v / 1000).toFixed(0)}k`;
  return `$${v.toFixed(0)}`;
}

function fmtMonth(m: string) {
  // "2025-11" → "Nov"
  const d = new Date(m + "-01");
  return d.toLocaleDateString("en-US", { month: "short" });
}

function fmtMonthYear(m: string) {
  const d = new Date(m + "-01");
  return d.toLocaleDateString("en-US", { month: "short", year: "2-digit" });
}

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
    <ResponsiveContainer width="100%" height={280}>
      <PieChart>
        <Pie
          data={data}
          cx="50%"
          cy="50%"
          innerRadius={70}
          outerRadius={110}
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
        <Legend
          formatter={(value) => {
            const item = data.find((d) => d.name === value);
            return `${value} ${item ? item.pct.toFixed(0) : ""}%`;
          }}
        />
        <text x="50%" y="48%" textAnchor="middle" fontSize={20} fontWeight="bold" fill="#374151">
          {fmtK(total)}
        </text>
        <text x="50%" y="58%" textAnchor="middle" fontSize={11} fill="#9ca3af">
          Total
        </text>
      </PieChart>
    </ResponsiveContainer>
  );
}

// ── Grouped Bars + Line: Income vs Expenses ────────────────────────────────

export function IncomeExpensesChart({
  data,
}: {
  data: MonthlyFlowPoint[];
}) {
  // Skip months with zero income (likely incomplete)
  const filtered = data.filter((d) => d.income > 0);

  return (
    <ResponsiveContainer width="100%" height={320}>
      <ComposedChart data={filtered} margin={{ top: 10, right: 40, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonth}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
        />
        <YAxis
          yAxisId="dollar"
          tickFormatter={fmtK}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          width={50}
        />
        <YAxis
          yAxisId="pct"
          orientation="right"
          tickFormatter={(v: number) => `${v.toFixed(0)}%`}
          fontSize={11}
          tick={{ fill: "#2563eb" }}
          domain={[0, 100]}
          width={40}
        />
        <Tooltip
          formatter={(value, name) => {
            const v = Number(value);
            if (name === "Savings %") return `${v.toFixed(1)}%`;
            return `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
          }}
          labelFormatter={(label) => fmtMonthYear(String(label))}
        />
        <Legend />
        <Bar yAxisId="dollar" dataKey="income" name="Income" fill="#27ae60" opacity={0.85} radius={[2, 2, 0, 0]} />
        <Bar yAxisId="dollar" dataKey="expenses" name="Expenses" fill="#e94560" opacity={0.85} radius={[2, 2, 0, 0]} />
        <Line
          yAxisId="pct"
          dataKey="savingsRate"
          name="Savings %"
          stroke="#2563eb"
          strokeWidth={2}
          strokeDasharray="4 3"
          dot={false}
        />
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
  if (data.length === 0) return null;

  return (
    <ResponsiveContainer width="100%" height={250}>
      <AreaChart data={data} margin={{ top: 10, right: 20, left: 10, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
        <XAxis
          dataKey="date"
          tickFormatter={(d: string) => {
            const dt = new Date(d);
            return dt.toLocaleDateString("en-US", { month: "short", day: "numeric" });
          }}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
        />
        <YAxis
          tickFormatter={fmtK}
          fontSize={11}
          tick={{ fill: "#9ca3af" }}
          width={55}
          domain={["dataMin - 10000", "dataMax + 10000"]}
        />
        <Tooltip
          formatter={(value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          labelFormatter={(label) => new Date(String(label)).toLocaleDateString("en-US", { month: "long", day: "numeric", year: "numeric" })}
        />
        <Area
          type="monotone"
          dataKey="total"
          stroke="#2563eb"
          fill="#dbeafe"
          fillOpacity={0.5}
          strokeWidth={2}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}
