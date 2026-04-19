"use client";

import { useEffect, useState } from "react";
import { ECON_URL, FETCH_TIMEOUT_MS } from "@/lib/config";
import { fetchWithSchema } from "@/lib/schemas/fetch-schema";
import { EconDataSchema, type EconData, type EconPoint } from "@/lib/schemas";
import { ECON_FORMATTERS } from "@/lib/format/econ-formatters";
import { fmtDateMedium } from "@/lib/format/format";
import { ECON_LINE_COLORS } from "@/lib/format/chart-colors";
import { Button } from "@/components/ui/button";
import { SectionHeader, SectionBody } from "@/components/finance/section";
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
  { dataKey: "fedFundsRate", label: "Fed Rate", color: ECON_LINE_COLORS.blue, formatter: ECON_FORMATTERS.fedFundsRate },
  { dataKey: "treasury10y", label: "10Y Treasury", color: ECON_LINE_COLORS.violet, formatter: ECON_FORMATTERS.treasury10y },
  { dataKey: "treasury2y", label: "2Y Treasury", color: ECON_LINE_COLORS.amber, formatter: ECON_FORMATTERS.treasury2y },
];
const SPREAD_LINES: LineConfig[] = [
  { dataKey: "spread2s10s", label: "2s10s Spread", color: ECON_LINE_COLORS.red, formatter: ECON_FORMATTERS.spread2s10s },
];
const INFLATION_LINES: LineConfig[] = [
  { dataKey: "cpiYoy", label: "CPI (YoY)", color: ECON_LINE_COLORS.red, formatter: ECON_FORMATTERS.cpiYoy },
  { dataKey: "coreCpiYoy", label: "Core CPI (YoY)", color: ECON_LINE_COLORS.amber, formatter: ECON_FORMATTERS.coreCpiYoy },
];
const UNEMPLOYMENT_LINES: LineConfig[] = [
  { dataKey: "unemployment", label: "Unemployment Rate", color: ECON_LINE_COLORS.blue, formatter: ECON_FORMATTERS.unemployment },
];
const VIX_LINES: LineConfig[] = [
  { dataKey: "vix", label: "VIX", color: ECON_LINE_COLORS.red, formatter: ECON_FORMATTERS.vix },
];
const OIL_LINES: LineConfig[] = [
  { dataKey: "oilWti", label: "WTI Crude", color: ECON_LINE_COLORS.green, formatter: ECON_FORMATTERS.oilWti },
];

// ── Economy Page ─────────────────────────────────────────────────────

export default function EconPage() {
  const [data, setData] = useState<EconData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [range, setRange] = useState<Range>("3Y");

  const fetchData = async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchWithSchema(ECON_URL, EconDataSchema, { cache: "no-store", timeoutMs: FETCH_TIMEOUT_MS }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load economic data");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchData(); }, []);

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
        Updated: {fmtDateMedium(data.generatedAt)}
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
