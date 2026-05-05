"use client";

import {
  Bar,
  BarChart,
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
import type { ApiCategory, MonthlyFlowPoint } from "@/lib/compute/computed-types";
import { fmtCurrencyShort, fmtDateMonthYear, fmtMonth } from "@/lib/format/format";
import { useIsDark } from "@/lib/hooks/use-is-dark";
import { useIsMobile } from "@/lib/hooks/use-is-mobile";
import { tooltipStyle, gridStroke, axisProps } from "@/lib/format/chart-styles";
import { TooltipCard } from "@/components/charts/tooltip-card";

// ── Donut: Category Allocation ─────────────────────────────────────────────

export function AllocationDonut({
  categories,
  total,
  colorByName,
}: {
  categories: ApiCategory[];
  total: number;
  colorByName: Record<string, string>;
}) {
  const isDark = useIsDark();
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
            {data.map((d) => (
              <Cell key={d.name} fill={colorByName[d.name]} />
            ))}
          </Pie>
          <Tooltip
            contentStyle={tooltipStyle(isDark)}
            formatter={(value) => `$${Number(value).toLocaleString("en-US", { maximumFractionDigits: 0 })}`}
          />
        </PieChart>
        {/* Center label — positioned with CSS, not SVG <text> */}
        <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none drop-shadow-[0_1px_2px_rgba(0,0,0,0.3)]">
          <span className="text-xl font-bold text-foreground">{fmtCurrencyShort(total)}</span>
          <span className="text-xs text-muted-foreground">Assets</span>
        </div>
      </div>
      {/* Legend — clean grid below */}
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 mt-2 text-sm">
        {data.map((d) => (
          <div key={d.name} className="flex items-center gap-1.5">
            <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0" style={{ backgroundColor: colorByName[d.name] }} />
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
  const row = payload?.[0]?.payload as MonthlyFlowPoint | undefined;
  const fmt = (v: number) => `$${v.toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
  return (
    <TooltipCard active={active} payload={payload} title={fmtDateMonthYear(String(label) + "-01")}>
      {(isDark) => (
        <>
          {row && <p style={{ color: isDark ? "#e5e7eb" : "#374151", margin: 0 }}>Income : {fmt(row.income)}</p>}
          {payload?.map((entry, i) => (
            <p key={i} style={{ color: entry.color, margin: 0 }}>
              {String(entry.name)} : {fmt(Number(entry.value))}
            </p>
          ))}
          {row && row.savingsRate !== 0 && (
            <p style={{ color: row.savingsRate > 0 ? "#22d3ee" : "#f87171", margin: 0 }}>
              Savings Rate : {Math.round(row.savingsRate)}%
            </p>
          )}
        </>
      )}
    </TooltipCard>
  );
}

// ── Stacked Bars: Expenses vs Savings ────────────────────────────────────

export function IncomeExpensesChart({
  data,
  activeMonth,
}: {
  data: MonthlyFlowPoint[];
  activeMonth?: string; // e.g. "2026-03"
}) {
  const isMobile = useIsMobile();
  const isDark = useIsDark();
  const activeIdx = activeMonth ? data.findIndex((d) => d.month === activeMonth) : -1;
  const expenseColor = isDark ? "#fb7185" : "#e94560";
  const savingsColor = isDark ? "#22d3ee" : "#0891b2";

  return (
    <ResponsiveContainer width="100%" height={isMobile ? 280 : 360}>
      <BarChart
        data={data}
        barCategoryGap="20%"
        margin={{ top: 20, right: 10, left: isMobile ? -5 : 10, bottom: 0 }}
      >
        <CartesianGrid vertical={false} stroke={gridStroke(isDark)} />
        <XAxis
          dataKey="month"
          tickFormatter={fmtMonth}
          {...axisProps(isDark)}
        />
        <YAxis
          tickFormatter={fmtCurrencyShort}
          width={isMobile ? 38 : 50}
          {...axisProps(isDark)}
          axisLine={false}
        />
        <Tooltip cursor={false} content={FlowTooltip} />
        <Legend verticalAlign="top" height={28} />
        {/* Leader line from active bar to stat bar above */}
        {activeIdx >= 0 && (
          <ReferenceLine
            x={data[activeIdx].month}
            stroke={isDark ? "rgba(255,255,255,0.12)" : "rgba(0,0,0,0.08)"}
            strokeDasharray="2 3"
            strokeWidth={1}
          />
        )}
        <Bar dataKey="expenses" name="Expenses" stackId="income" fill={expenseColor} isAnimationActive={false}>
          {data.map((_, i) => (
            <Cell key={i} fill={expenseColor} opacity={activeIdx >= 0 && i !== activeIdx ? 0.35 : 0.9} />
          ))}
        </Bar>
        <Bar dataKey="savings" name="Savings" stackId="income" fill={savingsColor} radius={[2, 2, 0, 0]} isAnimationActive={false}>
          {data.map((_, i) => (
            <Cell key={i} fill={savingsColor} opacity={activeIdx >= 0 && i !== activeIdx ? 0.35 : 0.9} />
          ))}
          <LabelList dataKey="savingsRate" content={SavingsLabel} />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
