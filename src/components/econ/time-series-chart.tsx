"use client";

import { useEffect, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import type { EconPoint } from "@/lib/econ-schema";

const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function fmtMonth(d: string) {
  const idx = parseInt(d.slice(5, 7), 10) - 1;
  const year = d.slice(2, 4);
  return `${MONTH_NAMES[idx] ?? d} ${year}`;
}

function useIsDark() {
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const check = () => setIsDark(document.documentElement.classList.contains("dark"));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return isDark;
}

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
          <CartesianGrid strokeDasharray="3 3" stroke={isDark ? "#334155" : "#e5e7eb"} />
          <XAxis dataKey="date" tickFormatter={fmtMonth} fontSize={11} tick={{ fill: "#9ca3af" }} interval="preserveStartEnd" />
          <YAxis fontSize={11} tick={{ fill: "#9ca3af" }} width={50} tickFormatter={lines[0]?.formatter ?? String} />
          <Tooltip
            contentStyle={{
              backgroundColor: isDark ? "#1e293b" : "#fff",
              border: `1px solid ${isDark ? "#334155" : "#e5e7eb"}`,
              borderRadius: "8px",
              padding: "8px 12px",
            }}
            labelFormatter={fmtMonth}
            formatter={(value: number, name: string) => {
              const line = lines.find((l) => l.dataKey === name);
              return [line?.formatter ? line.formatter(value) : value.toFixed(2), line?.label ?? name];
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
