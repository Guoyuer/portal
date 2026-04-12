import type { SnapshotPoint } from "@/lib/computed-types";
import { fmtCurrency, fmtPct } from "@/lib/format";
import { valueColor } from "@/lib/style-helpers";
import { SectionBody } from "@/components/finance/section";
import { NetWorthTrendChart } from "@/components/finance/charts";

export function NetWorthGrowth({ data: trend }: { data: SnapshotPoint[] }) {
  if (trend.length < 2) {
    return (
      <section>
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
      <SectionBody>
        {/* MoM / YoY badges — integrated into chart header */}
        <div className="flex items-center gap-4 mb-4">
          <div>
            <span className="text-xs text-muted-foreground mr-1.5">MoM</span>
            <span className={`text-sm font-semibold ${valueColor(mom)}`}>{fmtPct(mom, true)}</span>
            <span className={`text-xs ml-1 ${valueColor(momDelta)}`}>{fmtCurrency(momDelta)}</span>
          </div>
          <div className="w-px h-4 bg-border" />
          <div>
            <span className="text-xs text-muted-foreground mr-1.5">YoY</span>
            <span className={`text-sm font-semibold ${valueColor(yoy)}`}>{fmtPct(yoy, true)}</span>
            <span className={`text-xs ml-1 ${valueColor(yoyDelta)}`}>{fmtCurrency(yoyDelta)}</span>
          </div>
        </div>
        <NetWorthTrendChart data={trend} />
      </SectionBody>
    </section>
  );
}
