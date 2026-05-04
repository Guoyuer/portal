"use client";

import { useState, useEffect, useRef } from "react";
import { Area, AreaChart, YAxis } from "recharts";
import type { MarketData, IndexReturn } from "@/lib/schemas/timeline";
import { fmtPct } from "@/lib/format/format";
import { SectionHeader } from "@/components/finance/section";
import { MARKET_GAIN, MARKET_LOSS } from "@/lib/format/chart-colors";

function returnColor(v: number) { return v >= 0 ? MARKET_GAIN : MARKET_LOSS; }
// ~15% alpha (0x26 / 255 ≈ 0.15) — matches the previous `bg-[…]/15` Tailwind
// arbitrary class the JIT used to emit, without duplicating the hex in two places.
function returnBgAlpha(v: number) { return `${v >= 0 ? MARKET_GAIN : MARKET_LOSS}26`; }

// ── Badge ───────────────────────────────────────────────────────────────
function ReturnBadge({ label, value }: { label: string; value: number }) {
  return (
    <span
      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold tabular-nums"
      style={{ color: returnColor(value), backgroundColor: returnBgAlpha(value) }}
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

  const data = idx.sparkline.map((v) => ({ v }));
  const color = idx.ytdReturn >= 0 ? MARKET_GAIN : MARKET_LOSS;

  return (
    <div ref={ref} className="w-full h-full">
      {size.w > 0 && size.h > 0 && (
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
    <div className="mt-2" title="52-week range">
      <div className="flex justify-between text-[9px] tabular-nums text-muted-foreground mb-0.5">
        <span>{low.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
        <span>{high.toLocaleString("en-US", { maximumFractionDigits: 0 })}</span>
      </div>
      <div className="relative h-1 rounded-full bg-foreground/10">
        <div
          className="absolute top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-foreground/70"
          style={{ left: `calc(${Math.min(Math.max(pct, 0), 100)}% - 4px)` }}
        />
      </div>
      <p className="text-[9px] text-foreground/30 uppercase tracking-widest mt-0.5">52-week range</p>
    </div>
  );
}

// ── Index Card ──────────────────────────────────────────────────────────
function IndexCard({ idx }: { idx: IndexReturn }) {
  const pts = idx.current >= 1000
    ? idx.current.toLocaleString("en-US", { maximumFractionDigits: 0 })
    : idx.current.toFixed(2);

  return (
    <div
      className="liquid-glass-thin p-3 flex flex-col justify-between min-h-[160px]"
      style={{ borderColor: "rgba(255,255,255,0.15)" }}
    >
      <div className="flex items-start justify-between">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-foreground/60 tracking-wide uppercase">
            {idx.name}
          </p>
          <p className="text-xl font-bold tabular-nums text-foreground mt-1">
            {pts}
          </p>
        </div>
        <div className="flex flex-col items-end gap-0.5">
          <ReturnBadge label="M" value={idx.monthReturn} />
          <ReturnBadge label="YTD" value={idx.ytdReturn} />
        </div>
      </div>
      <div className="-mx-1 -mt-0.5 h-[60px]">
        <Sparkline idx={idx} />
      </div>
      <RangeBar current={idx.current} high={idx.high52w} low={idx.low52w} />
    </div>
  );
}

// ── MarketContext ────────────────────────────────────────────────────────
export function MarketContext({ data: m, title }: { data: MarketData; title: string }) {
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
    </section>
  );
}
