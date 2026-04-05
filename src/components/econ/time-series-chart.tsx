"use client";

import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { EconPoint } from "@/lib/econ-schema";
import { fmtMonthYear } from "@/lib/format";
import { useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke } from "@/lib/chart-styles";

export interface LineConfig {
  dataKey: string;
  label: string;
  color: string;
  formatter?: (v: number) => string;
}

interface TimeSeriesChartProps {
  title: string;
  lines: LineConfig[];
  data: Record<string, EconPoint[]>;
  height?: number;
}

export function TimeSeriesChart({ title, lines, data, height = 280 }: TimeSeriesChartProps) {
  const isDark = useIsDark();

  // Merge all series into unified date-keyed rows
  const dateMap = new Map<string, Record<string, number>>();
  for (const line of lines) {
    const points = data[line.dataKey] ?? [];
    for (const p of points) {
      const row = dateMap.get(p.date) ?? {};
      row[line.dataKey] = p.value;
      dateMap.set(p.date, row);
    }
  }

  const merged = Array.from(dateMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({ date, ...values }));

  if (merged.length === 0) return null;

  return (
    <div>
      <h3 className="font-semibold mb-3">{title}</h3>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={merged} margin={{ top: 5, right: 20, left: 10, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={gridStroke(isDark)} />
          <XAxis dataKey="date" tickFormatter={fmtMonthYear} fontSize={11} tick={{ fill: "#9ca3af" }} interval="preserveStartEnd" />
          <YAxis fontSize={11} tick={{ fill: "#9ca3af" }} width={50} tickFormatter={lines[0]?.formatter ?? String} />
          <Tooltip
            contentStyle={tooltipStyle(isDark)}
            labelFormatter={(label) => fmtMonthYear(String(label))}
            formatter={(value, name) => {
              const v = Number(value);
              const n = String(name);
              const line = lines.find((l) => l.dataKey === n);
              return [line?.formatter ? line.formatter(v) : v.toFixed(2), line?.label ?? n];
            }}
          />
          {lines.length > 1 && <Legend />}
          {lines.map((line) => (
            <Line key={line.dataKey} dataKey={line.dataKey} name={line.label} stroke={line.color} strokeWidth={2} dot={false} connectNulls />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
