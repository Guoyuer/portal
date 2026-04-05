import type { EconSnapshot } from "@/lib/econ-schema";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const INDICATORS: { key: keyof EconSnapshot; label: string; format: (v: number) => string }[] = [
  { key: "fedFundsRate", label: "Fed Rate", format: (v) => `${v.toFixed(2)}%` },
  { key: "treasury10y", label: "10Y Treasury", format: (v) => `${v.toFixed(2)}%` },
  { key: "spread2s10s", label: "2s10s Spread", format: (v) => `${v >= 0 ? "+" : ""}${(v * 100).toFixed(0)} bps` },
  { key: "cpiYoy", label: "CPI (YoY)", format: (v) => `${v.toFixed(1)}%` },
  { key: "unemployment", label: "Unemployment", format: (v) => `${v.toFixed(1)}%` },
  { key: "vix", label: "VIX", format: (v) => v.toFixed(1) },
  { key: "dxy", label: "DXY", format: (v) => v.toFixed(1) },
  { key: "oilWti", label: "Oil (WTI)", format: (v) => `$${v.toFixed(0)}` },
  { key: "usdCny", label: "USD/CNY", format: (v) => v.toFixed(4) },
];

export function MacroCards({ snapshot }: { snapshot: EconSnapshot }) {
  const visible = INDICATORS.filter((ind) => snapshot[ind.key] != null);
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-3">
      {visible.map((ind) => (
        <Card key={ind.key}>
          <CardHeader className="pb-1">
            <CardTitle className="text-xs text-muted-foreground">{ind.label}</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-lg font-bold">{ind.format(snapshot[ind.key]!)}</p>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
