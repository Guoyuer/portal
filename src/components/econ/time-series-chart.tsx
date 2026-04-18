"use client";

import { useId } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { EconPoint } from "@/lib/schemas";
import { fmtMonthYear } from "@/lib/format";
import { useIsDark } from "@/lib/hooks";
import { tooltipStyle, gridStroke, legendStyle } from "@/lib/chart-styles";

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

/** Merge multiple date-keyed series into a single sorted array of
 * { date, <key1>, <key2>, ... } rows. Rows include only the keys that had a
 * value at that date; recharts' `connectNulls` handles gaps. */
export function mergeSeriesByDate(
  lines: LineConfig[],
  data: Record<string, EconPoint[]>,
): { date: string; [key: string]: number | string }[] {
  const dateMap = new Map<string, Record<string, number>>();
  for (const line of lines) {
    const points = data[line.dataKey] ?? [];
    for (const p of points) {
      const row = dateMap.get(p.date) ?? {};
      row[line.dataKey] = p.value;
      dateMap.set(p.date, row);
    }
  }
  return Array.from(dateMap.entries())
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([date, values]) => ({ date, ...values }));
}

export function TimeSeriesChart({ title, lines, data, height = 280 }: TimeSeriesChartProps) {
  const isDark = useIsDark();
  const filterId = useId();

  const merged = mergeSeriesByDate(lines, data);
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
            <Legend wrapperStyle={legendStyle(isDark)} />
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
