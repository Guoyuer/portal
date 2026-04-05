import type { EconSnapshot } from "@/lib/econ-schema";

const INDICATORS: { key: keyof EconSnapshot; label: string; format: (v: number) => string }[] = [
  { key: "fedFundsRate", label: "Fed Rate", format: (v) => `${v.toFixed(2)}%` },
  { key: "treasury10y", label: "10Y Treasury", format: (v) => `${v.toFixed(2)}%` },
  { key: "spread2s10s", label: "2s10s Spread", format: (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)} bps` },
  { key: "cpiYoy", label: "CPI (YoY)", format: (v) => `${v.toFixed(1)}%` },
  { key: "coreCpiYoy", label: "Core CPI", format: (v) => `${v.toFixed(1)}%` },
  { key: "unemployment", label: "Unemployment", format: (v) => `${v.toFixed(1)}%` },
  { key: "vix", label: "VIX", format: (v) => v.toFixed(1) },
  { key: "dxy", label: "DXY", format: (v) => v.toFixed(1) },
  { key: "oilWti", label: "Oil (WTI)", format: (v) => `$${v.toFixed(0)}` },
  { key: "usdCny", label: "USD/CNY", format: (v) => v.toFixed(4) },
];

export function MacroCards({ snapshot }: { snapshot: EconSnapshot }) {
  const visible = INDICATORS.filter((ind) => snapshot[ind.key] != null);
  return (
    <div className="grid grid-cols-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2 sm:gap-3">
      {visible.map((ind) => (
        <div key={ind.key} className="liquid-glass-thin px-3 sm:px-4 pt-2 sm:pt-3 pb-2 sm:pb-3">
          <p className="text-[10px] sm:text-xs text-muted-foreground">{ind.label}</p>
          <p className="text-sm sm:text-lg font-bold mt-0.5">{ind.format(snapshot[ind.key]!)}</p>
        </div>
      ))}
    </div>
  );
}
