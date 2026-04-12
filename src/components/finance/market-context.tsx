"use client";

import { useState, useEffect, useRef } from "react";
import { Area, AreaChart, YAxis } from "recharts";
import type { MarketData, IndexReturn } from "@/lib/schema";
import { fmtPct } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import { SectionHeader } from "@/components/finance/shared";

// ── Display name mapping ────────────────────────────────────────────────
const INDEX_NAMES: Record<string, string> = {
  "^GSPC": "S&P 500",
  "^NDX": "NASDAQ 100",
};

// ── Palette ─────────────────────────────────────────────────────────────
const GAIN = "#81b29a";
const LOSS = "#cd6155";

function returnColor(v: number) { return v >= 0 ? GAIN : LOSS; }
function returnBg(v: number) { return v >= 0 ? "bg-[#81b29a]/15" : "bg-[#cd6155]/15"; }

// ── Badge ───────────────────────────────────────────────────────────────
function ReturnBadge({ label, value }: { label: string; value: number }) {
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold tabular-nums ${returnBg(value)}`}
      style={{ color: returnColor(value) }}
    >
      <span className="text-[9px] opacity-60 font-normal">{label}</span>
      {fmtPct(value, true)}
    </span>
  );
}

// ── Sparkline ───────────────────────────────────────────────────────────
function Sparkline({ idx }: { idx: IndexReturn }) {
  const ref = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) setSize({ w: Math.floor(width), h: Math.floor(height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const data = idx.sparkline?.map((v) => ({ v }));
  const color = idx.ytdReturn >= 0 ? GAIN : LOSS;

  return (
    <div ref={ref} className="w-full h-full">
      {data && size.w > 0 && size.h > 0 && (
        <AreaChart width={size.w} height={size.h} data={data} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id={`spark-${idx.ticker}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.3} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <YAxis domain={["dataMin", "dataMax"]} hide />
          <Area
            type="monotone"
            dataKey="v"
            stroke={color}
            strokeWidth={1.5}
            fill={`url(#spark-${idx.ticker})`}
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      )}
    </div>
  );
}

// ── 52-Week Range Bar ───────────────────────────────────────────────────
function RangeBar({ current, high, low }: { current: number; high: number; low: number }) {
  const range = high - low;
  const pct = range > 0 ? ((current - low) / range) * 100 : 50;

  return (
    <div className="mt-2">
      <div className="flex justify-between text-[9px] tabular-nums text-muted-foreground mb-0.5">
        <span>{low.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
        <span className="opacity-50">52W</span>
        <span>{high.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
      </div>
      <div className="relative h-1 rounded-full bg-foreground/10">
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-foreground/70"
          style={{ left: `calc(${Math.min(Math.max(pct, 0), 100)}% - 4px)` }}
        />
      </div>
    </div>
  );
}

// ── Index Card ──────────────────────────────────────────────────────────
function IndexCard({ idx }: { idx: IndexReturn }) {
  const displayName = INDEX_NAMES[idx.ticker] ?? idx.name;
  const pts = idx.current >= 1000
    ? idx.current.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : idx.current.toFixed(2);

  return (
    <div
      className="liquid-glass-thin p-3 flex flex-col justify-between min-h-[160px]"
      style={{ borderColor: "rgba(255,255,255,0.15)" }}
    >
      <div className="flex items-start justify-between">
        <p className="text-xs font-semibold text-foreground/60 tracking-wide uppercase">
          {displayName}
        </p>
        <div className="flex flex-col items-end gap-0.5">
          <ReturnBadge label="M" value={idx.monthReturn} />
          <ReturnBadge label="YTD" value={idx.ytdReturn} />
        </div>
      </div>
      <p className="text-xl font-bold tabular-nums text-foreground -mt-1.5">
        {pts}
      </p>
      <div className="-mx-1 -mt-0.5 h-[60px]">
        <Sparkline idx={idx} />
      </div>
      {idx.high52w != null && idx.low52w != null && (
        <RangeBar current={idx.current} high={idx.high52w} low={idx.low52w} />
      )}
    </div>
  );
}

// ── MarketContext ────────────────────────────────────────────────────────
const INDICATOR_DEFS: { key: keyof MarketData; label: string; fmt: (v: number) => string }[] = [
  { key: "fedRate",      label: "Fed Rate",     fmt: (v) => fmtPct(v, false) },
  { key: "treasury10y",  label: "10Y Treasury", fmt: (v) => fmtPct(v, false) },
  { key: "cpi",          label: "CPI",          fmt: (v) => fmtPct(v, false) },
  { key: "unemployment", label: "Unemployment", fmt: (v) => fmtPct(v, false) },
  { key: "vix",          label: "VIX",          fmt: (v) => v.toFixed(1) },
  { key: "dxy",          label: "DXY",          fmt: (v) => v.toFixed(1) },
  { key: "usdCny",       label: "USD/CNY",      fmt: (v) => v.toFixed(4) },
];

export function MarketContext({ data: m, title }: { data: MarketData; title: string }) {
  const indicators = INDICATOR_DEFS
    .filter(({ key }) => m[key] != null)
    .map(({ key, label, fmt }) => ({ label, value: fmt(m[key] as number) }));

  return (
    <section>
      <SectionHeader>{title}</SectionHeader>

      {/* Index Cards — directly on page background, no outer glass wrapper */}
      {m.indices.length > 0 ? (
        <div className="@container grid grid-cols-2 @md:grid-cols-4 gap-3">
          {m.indices.map((idx) => (
            <IndexCard key={idx.ticker} idx={idx} />
          ))}
        </div>
      ) : (
        <p className="text-sm text-red-400">Index data unavailable</p>
      )}

      {/* Macro Indicators */}
      {indicators.length > 0 ? (
        <div className="@container liquid-glass p-3 sm:p-5 mt-4">
          <h3 className="text-xs font-semibold text-foreground/50 uppercase tracking-wider mb-2">
            Macro
          </h3>
          <div className="grid grid-cols-2 @md:grid-cols-4 gap-x-6 gap-y-1.5 text-sm">
            {indicators.map((ind) => (
              <div
                key={ind.label}
                className="flex justify-between py-1 border-b border-foreground/5"
              >
                <span className="text-muted-foreground text-xs">{ind.label}</span>
                <span className="font-medium text-xs tabular-nums">
                  {ind.value}
                </span>
              </div>
            ))}
          </div>
        </div>
      ) : (
        <p className="text-sm text-red-400 mt-4">Macro data unavailable</p>
      )}
    </section>
  );
}
