import type { EconSnapshot } from "@/lib/schemas";
import { ECON_FORMATTERS, fmtSpreadSigned } from "@/lib/format/econ-formatters";

const INDICATORS: { key: keyof EconSnapshot; label: string; format: (v: number) => string }[] = [
  { key: "fedFundsRate", label: "Fed Rate", format: ECON_FORMATTERS.fedFundsRate },
  { key: "treasury10y", label: "10Y Treasury", format: ECON_FORMATTERS.treasury10y },
  { key: "spread2s10s", label: "2s10s Spread", format: fmtSpreadSigned },
  { key: "cpiYoy", label: "CPI (YoY)", format: ECON_FORMATTERS.cpiYoy },
  { key: "coreCpiYoy", label: "Core CPI", format: ECON_FORMATTERS.coreCpiYoy },
  { key: "unemployment", label: "Unemployment", format: ECON_FORMATTERS.unemployment },
  { key: "vix", label: "VIX", format: ECON_FORMATTERS.vix },
  { key: "dxy", label: "DXY", format: ECON_FORMATTERS.dxy },
  { key: "oilWti", label: "Oil (WTI)", format: ECON_FORMATTERS.oilWti },
  { key: "usdCny", label: "USD/CNY", format: ECON_FORMATTERS.usdCny },
];

export function MacroCards({ snapshot }: { snapshot: EconSnapshot }) {
  const visible = INDICATORS.filter((ind) => snapshot[ind.key] != null);
  return (
    <div className="grid grid-cols-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-2 sm:gap-3">
      {visible.map((ind) => (
        <div key={ind.key} className="liquid-glass-thin px-3 sm:px-4 pt-2 sm:pt-3 pb-2 sm:pb-3">
          <p className="text-[10px] sm:text-xs text-muted-foreground">{ind.label}</p>
          <p className="text-base sm:text-lg font-bold mt-0.5">{ind.format(snapshot[ind.key]!)}</p>
        </div>
      ))}
    </div>
  );
}
