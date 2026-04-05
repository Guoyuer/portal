"use client";

import { useId } from "react";
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
  const filterId = useId();

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
          <XAxis dataKey="date" tickFormatter={fmtMonthYear} fontSize={11} tick={{ fill: "#9ca3af" }} axisLine={{ stroke: gridStroke(isDark) }} tickLine={false} interval="preserveStartEnd" />
          <YAxis fontSize={11} tick={{ fill: "#9ca3af" }} width={50} tickFormatter={lines[0]?.formatter ?? String} axisLine={false} tickLine={false} />
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
          {lines.length > 1 && (
            <Legend
              wrapperStyle={{
                paddingTop: "8px",
                background: isDark ? "rgba(255,255,255,0.03)" : "rgba(255,255,255,0.4)",
                backdropFilter: "blur(12px)",
                WebkitBackdropFilter: "blur(12px)",
                borderRadius: "10px",
                padding: "4px 12px",
                border: `1px solid ${isDark ? "rgba(255,255,255,0.06)" : "rgba(255,255,255,0.3)"}`,
              }}
            />
          )}
          {lines.map((line) => (
            <Line key={line.dataKey} dataKey={line.dataKey} name={line.label} stroke={line.color} strokeWidth={2} dot={false} connectNulls filter={`url(#${filterId})`} />
          ))}
          <defs>
            <filter id={filterId} x="-2%" y="-2%" width="104%" height="104%">
              <feDropShadow dx="0" dy="1" stdDeviation="2" floodColor={isDark ? "#000" : "#fff"} floodOpacity={isDark ? 0.4 : 0.6} />
            </filter>
          </defs>
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
