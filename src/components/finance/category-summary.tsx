"use client";

import type { ApiCategory, ApiTicker } from "@/lib/compute/computed-types";
import { SectionHeader, SectionBody } from "@/components/finance/section";
import { AllocationDonut } from "@/components/finance/charts";
import { CategoryAllocationTable } from "@/components/finance/category-allocation-table";
import { buildCategorySummaryModel, groupTickers } from "@/components/finance/category-summary-model";

export { groupTickers };

export function CategorySummary({
  categories,
  tickers,
  total: totalValue,
  title,
  embedded,
  colorByName,
}: {
  categories: ApiCategory[];
  tickers: ApiTicker[];
  total: number;
  title: string;
  embedded?: boolean;
  colorByName: Record<string, string>;
}) {
  const model = buildCategorySummaryModel(categories, tickers, totalValue);

  const inner = (
    <div className="flex flex-col lg:flex-row gap-6">
      <div className="flex-1 min-w-0 overflow-x-auto scrollbar-none">
        <CategoryAllocationTable model={model} totalValue={totalValue} />
      </div>
      <div className="lg:w-80 flex-shrink-0">
        <AllocationDonut categories={model.donutCategories} total={totalValue} colorByName={colorByName} />
      </div>
    </div>
  );

  if (embedded) return inner;

  return (
    <section>
      <SectionHeader>{title}</SectionHeader>
      <SectionBody>{inner}</SectionBody>
    </section>
  );
}
