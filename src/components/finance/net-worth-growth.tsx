import type { SnapshotPoint } from "@/lib/types";
import { fmtCurrency, fmtPct } from "@/lib/format";
import { SectionHeader, SectionBody } from "@/components/finance/shared";
import { NetWorthTrendChart } from "@/components/finance/charts";

export function NetWorthGrowth({ data: trend }: { data: SnapshotPoint[] }) {
  if (trend.length < 2) {
    return (
      <section>
        <SectionHeader>Net Worth</SectionHeader>
        <SectionBody>
          {trend.length === 1 ? (
            <NetWorthTrendChart data={trend} />
          ) : (
            <p className="text-sm text-muted-foreground">Not enough data points yet.</p>
          )}
        </SectionBody>
      </section>
    );
  }

  const latest = trend[trend.length - 1];
  const prev = trend[trend.length - 2];
  const mom = prev.total > 0 ? (latest.total - prev.total) / prev.total * 100 : 0;
  const momDelta = latest.total - prev.total;

  // YoY: find entry ~12 months ago
  const latestDate = new Date(latest.date);
  const yoyTarget = new Date(latestDate);
  yoyTarget.setFullYear(yoyTarget.getFullYear() - 1);
  const yoyEntry = trend.reduce((best, entry) => {
    const d = new Date(entry.date);
    return Math.abs(d.getTime() - yoyTarget.getTime()) < Math.abs(new Date(best.date).getTime() - yoyTarget.getTime()) ? entry : best;
  });
  const yoy = yoyEntry.total > 0 ? (latest.total - yoyEntry.total) / yoyEntry.total * 100 : 0;
  const yoyDelta = latest.total - yoyEntry.total;

  return (
    <section>
      <SectionHeader>Net Worth</SectionHeader>
      <SectionBody>
        <div className="grid grid-cols-2 gap-6 mb-4">
          <div className="text-center p-4">
            <p className="text-sm text-muted-foreground">MoM Change</p>
            <p className={`text-3xl font-bold ${mom >= 0 ? "text-green-600" : "text-red-500"}`}>
              {fmtPct(mom)}
            </p>
            <p className={`text-sm ${momDelta >= 0 ? "text-green-600" : "text-red-500"}`}>
              {fmtCurrency(momDelta)}
            </p>
          </div>
          <div className="text-center p-4">
            <p className="text-sm text-muted-foreground">YoY Change</p>
            <p className={`text-3xl font-bold ${yoy >= 0 ? "text-green-600" : "text-red-500"}`}>
              {fmtPct(yoy)}
            </p>
            <p className={`text-sm ${yoyDelta >= 0 ? "text-green-600" : "text-red-500"}`}>
              {fmtCurrency(yoyDelta)}
            </p>
          </div>
        </div>
        <NetWorthTrendChart data={trend} />
      </SectionBody>
    </section>
  );
}
