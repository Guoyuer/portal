"use client";

import { useCallback, useEffect, useState } from "react";
import { ECON_URL } from "@/lib/config";
import { EconDataSchema, type EconData, type EconPoint } from "@/lib/econ-schema";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { MacroCards } from "@/components/econ/macro-cards";
import { TimeSeriesChart, type LineConfig } from "@/components/econ/time-series-chart";
import { BackToTop } from "@/components/layout/back-to-top";
import { EconSkeleton } from "@/components/loading-skeleton";
import { ErrorBoundary, SectionError } from "@/components/error-boundary";

// ── Range filter ─────────────────────────────────────────────────────

type Range = "1Y" | "3Y" | "5Y";
const RANGES: Range[] = ["1Y", "3Y", "5Y"];
const RANGE_MONTHS: Record<Range, number> = { "1Y": 12, "3Y": 36, "5Y": 60 };

function filterSeries(series: Record<string, EconPoint[]>, months: number): Record<string, EconPoint[]> {
  const cutoff = new Date();
  cutoff.setMonth(cutoff.getMonth() - months);
  const cutoffStr = cutoff.toISOString().slice(0, 7);
  const result: Record<string, EconPoint[]> = {};
  for (const [key, points] of Object.entries(series)) {
    result[key] = points.filter((p) => p.date >= cutoffStr);
  }
  return result;
}

// ── Line configs ─────────────────────────────────────────────────────

const RATE_LINES: LineConfig[] = [
  { dataKey: "fedFundsRate", label: "Fed Rate", color: "#2563eb", formatter: (v) => `${v.toFixed(2)}%` },
  { dataKey: "treasury10y", label: "10Y Treasury", color: "#7c3aed", formatter: (v) => `${v.toFixed(2)}%` },
  { dataKey: "treasury2y", label: "2Y Treasury", color: "#f59e0b", formatter: (v) => `${v.toFixed(2)}%` },
];
const SPREAD_LINES: LineConfig[] = [
  { dataKey: "spread2s10s", label: "2s10s Spread", color: "#ef4444", formatter: (v) => `${(v * 100).toFixed(0)} bps` },
];
const INFLATION_LINES: LineConfig[] = [
  { dataKey: "cpiYoy", label: "CPI (YoY)", color: "#ef4444", formatter: (v) => `${v.toFixed(1)}%` },
  { dataKey: "coreCpiYoy", label: "Core CPI (YoY)", color: "#f59e0b", formatter: (v) => `${v.toFixed(1)}%` },
];
const UNEMPLOYMENT_LINES: LineConfig[] = [
  { dataKey: "unemployment", label: "Unemployment Rate", color: "#2563eb", formatter: (v) => `${v.toFixed(1)}%` },
];
const VIX_LINES: LineConfig[] = [
  { dataKey: "vix", label: "VIX", color: "#ef4444", formatter: (v) => v.toFixed(1) },
];
const OIL_LINES: LineConfig[] = [
  { dataKey: "oilWti", label: "WTI Crude", color: "#10b981", formatter: (v) => `$${v.toFixed(0)}` },
];

// ── Economy Page ─────────────────────────────────────────────────────

export default function EconPage() {
  const [data, setData] = useState<EconData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<Range>("3Y");

  const fetchData = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(ECON_URL, { cache: "no-store" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      const json = await res.json();
      const parsed = EconDataSchema.safeParse(json);
      if (!parsed.success) {
        throw new Error(`Invalid econ data: ${parsed.error.issues[0]?.message ?? "schema mismatch"}`);
      }
      setData(parsed.data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load economic data");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const filtered = data ? filterSeries(data.series, RANGE_MONTHS[range]) : {};

  if (loading) return <EconSkeleton />;

  if (error || !data) {
    return (
      <div className="max-w-5xl mx-auto py-20 text-center">
        <p className="text-red-500 mb-4">{error ?? "No data"}</p>
        <Button onClick={fetchData} variant="outline">Retry</Button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto space-y-10">
      {/* Header */}
      <h1 className="text-xl sm:text-2xl font-semibold tracking-tight">
        Economy Dashboard
      </h1>

      <p className="text-xs text-foreground/50 -mt-4">
        Updated: {new Date(data.generatedAt).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}
        {" "}
        {new Date(data.generatedAt).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" })}
      </p>

      {/* Macro snapshot cards */}
      <ErrorBoundary fallback={<SectionError label="Macro Cards" />}>
        <MacroCards snapshot={data.snapshot} />
      </ErrorBoundary>

      {/* Range toggle — controls chart time range below */}
      <div className="flex gap-1 liquid-glass-pill rounded-full p-1 w-fit">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setRange(r)}
            className={`px-4 py-1.5 rounded-full text-sm font-medium transition-colors ${
              range === r
                ? "bg-white/40 dark:bg-white/12 text-foreground shadow-sm backdrop-blur-sm border border-white/30"
                : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {r}
          </button>
        ))}
      </div>

      {/* ── Interest Rates ──────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Interest Rates" />}>
        <section>
          <SectionHeader>Interest Rates</SectionHeader>
          <SectionBody>
            <TimeSeriesChart title="Fed Rate, 10Y & 2Y Treasury" lines={RATE_LINES} data={filtered} />
            <div className="mt-6 pt-6 border-t border-border">
              <TimeSeriesChart title="2s10s Yield Spread" lines={SPREAD_LINES} data={filtered} />
            </div>
          </SectionBody>
        </section>
      </ErrorBoundary>

      {/* ── Inflation ───────────────────────────────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Inflation" />}>
        <section>
          <SectionHeader>Inflation</SectionHeader>
          <SectionBody>
            <TimeSeriesChart title="CPI & Core CPI (Year-over-Year)" lines={INFLATION_LINES} data={filtered} />
          </SectionBody>
        </section>
      </ErrorBoundary>

      {/* ── Labor / Sentiment / Commodities ──────────────────────────── */}
      <ErrorBoundary fallback={<SectionError label="Labor / Sentiment / Commodities" />}>
        <section>
          <SectionHeader>Labor / Sentiment / Commodities</SectionHeader>
          <SectionBody>
            <TimeSeriesChart title="Unemployment Rate" lines={UNEMPLOYMENT_LINES} data={filtered} />
            <div className="mt-6 pt-6 border-t border-border">
              <TimeSeriesChart title="VIX (Volatility Index)" lines={VIX_LINES} data={filtered} />
            </div>
            <div className="mt-6 pt-6 border-t border-border">
              <TimeSeriesChart title="Oil WTI" lines={OIL_LINES} data={filtered} />
            </div>
          </SectionBody>
        </section>
      </ErrorBoundary>

      <BackToTop />
    </div>
  );
}
