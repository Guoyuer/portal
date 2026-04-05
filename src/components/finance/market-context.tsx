import type { MarketData } from "@/lib/types";
import { fmtCurrency, fmtPct } from "@/lib/format";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SectionHeader, SectionBody } from "@/components/finance/shared";

export function MarketContext({ data: m }: { data: MarketData }) {
  const indicators: { label: string; value: string; color?: string }[] = [];
  if (m.fedRate != null)
    indicators.push({ label: "Fed Rate", value: fmtPct(m.fedRate, false) });
  if (m.treasury10y != null)
    indicators.push({ label: "10Y Treasury", value: fmtPct(m.treasury10y, false) });
  if (m.cpi != null)
    indicators.push({ label: "CPI", value: fmtPct(m.cpi, false) });
  if (m.unemployment != null)
    indicators.push({ label: "Unemployment", value: fmtPct(m.unemployment, false) });
  if (m.vix != null)
    indicators.push({ label: "VIX", value: m.vix.toFixed(1) });
  if (m.dxy != null)
    indicators.push({ label: "DXY", value: m.dxy.toFixed(1) });
  if (m.usdCny != null)
    indicators.push({ label: "USD/CNY", value: m.usdCny.toFixed(4) });
  if (m.goldReturn != null)
    indicators.push({
      label: "Gold",
      value: fmtPct(m.goldReturn),
      color: m.goldReturn >= 0 ? "text-green-600" : "text-red-500",
    });
  if (m.btcReturn != null)
    indicators.push({
      label: "Bitcoin",
      value: fmtPct(m.btcReturn),
      color: m.btcReturn >= 0 ? "text-green-600" : "text-red-500",
    });

  return (
    <section>
      <SectionHeader>Market Context</SectionHeader>
      <SectionBody>
        {/* Index Returns */}
        {m.indices.length > 0 && (
          <div>
            <h3 className="font-semibold mb-2">Index Returns</h3>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Index</TableHead>
                  <TableHead className="text-right">Current</TableHead>
                  <TableHead className="text-right">Month</TableHead>
                  <TableHead className="text-right">YTD</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {m.indices.map((idx) => (
                  <TableRow key={idx.ticker} className="even:bg-muted/50">
                    <TableCell className="font-medium">
                      {idx.name}{" "}
                      <span className="text-muted-foreground font-mono text-xs">
                        {idx.ticker}
                      </span>
                    </TableCell>
                    <TableCell className="text-right">
                      {fmtCurrency(idx.current)}
                    </TableCell>
                    <TableCell
                      className={`text-right ${idx.monthReturn >= 0 ? "text-green-600" : "text-red-500"}`}
                    >
                      {fmtPct(idx.monthReturn)}
                    </TableCell>
                    <TableCell
                      className={`text-right ${idx.ytdReturn >= 0 ? "text-green-600" : "text-red-500"}`}
                    >
                      {fmtPct(idx.ytdReturn)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        {/* Macro Indicators */}
        {indicators.length > 0 && (
          <div className="mt-6">
            <h3 className="font-semibold mb-2">Macro Indicators</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-8 gap-y-2">
              {indicators.map((ind) => (
                <div
                  key={ind.label}
                  className="flex justify-between py-1.5 border-b border-border"
                >
                  <span className="text-muted-foreground">{ind.label}</span>
                  <span className={`font-medium ${ind.color ?? ""}`}>
                    {ind.value}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </SectionBody>
    </section>
  );
}
