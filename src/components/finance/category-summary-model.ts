import type { ApiCategory, ApiTicker } from "@/lib/compute/computed-types";

// -- Equity categories for classification -----------------------------------

const EQUITY_CATEGORIES = new Set(["US Equity", "Non-US Equity", "Crypto"]);

export interface GroupedCategory {
  name: string;
  value: number;
  pct: number;
  target: number;
  deviation: number;
  isEquity: boolean;
  subtypes: { name: string; tickers: ApiTicker[]; value: number; pct: number }[];
}

interface CategoryAggregate {
  value: number;
  pct: number;
  target: number;
  deviation: number;
}

export interface CategorySummaryModel {
  grouped: GroupedCategory[];
  equityCats: GroupedCategory[];
  nonEquityCats: GroupedCategory[];
  nonEquityAggregate: CategoryAggregate | null;
  totalPct: number;
  totalTarget: number;
  totalDeviation: number;
}

/** Sum value/pct/target across categories; deviation is pct - target of the sum. */
function aggregateCategories(cats: Pick<GroupedCategory, "value" | "pct" | "target">[]): CategoryAggregate {
  let value = 0;
  let pct = 0;
  let target = 0;
  for (const c of cats) {
    value += c.value;
    pct += c.pct;
    target += c.target;
  }
  return { value, pct, target, deviation: pct - target };
}

function isEquityCategory(name: string): boolean {
  return EQUITY_CATEGORIES.has(name);
}

function groupTickers(categories: ApiCategory[], tickers: ApiTicker[], total: number): GroupedCategory[] {
  const tickersByCategory: Record<string, Record<string, ApiTicker[]>> = {};
  for (const t of tickers) {
    if (!tickersByCategory[t.category]) tickersByCategory[t.category] = {};
    const sub = t.subtype || "(other)";
    if (!tickersByCategory[t.category][sub]) tickersByCategory[t.category][sub] = [];
    tickersByCategory[t.category][sub].push(t);
  }

  return categories.map((cat) => {
    const subs = tickersByCategory[cat.name] ?? {};
    const subtypes = Object.entries(subs).map(([name, ts]) => {
      const sortedTickers = [...ts].sort((a, b) => b.value - a.value);
      const subValue = sortedTickers.reduce((s, t) => s + t.value, 0);
      return {
        name,
        tickers: sortedTickers,
        value: subValue,
        pct: total > 0 ? (subValue / total) * 100 : 0,
      };
    });
    return {
      name: cat.name,
      value: cat.value,
      pct: cat.pct,
      target: cat.target,
      deviation: cat.deviation,
      isEquity: isEquityCategory(cat.name),
      subtypes,
    };
  });
}

export function buildCategorySummaryModel(
  categories: ApiCategory[],
  tickers: ApiTicker[],
  totalValue: number,
): CategorySummaryModel {
  const grouped = groupTickers(categories, tickers, totalValue);
  const equityCats = grouped.filter((c) => c.isEquity);
  const nonEquityCats = grouped.filter((c) => !c.isEquity);

  const totalPct = categories.reduce((s, c) => s + c.pct, 0);
  const totalTarget = categories.reduce((s, c) => s + c.target, 0);

  return {
    grouped,
    equityCats,
    nonEquityCats,
    nonEquityAggregate: nonEquityCats.length > 0 ? aggregateCategories(nonEquityCats) : null,
    totalPct,
    totalTarget,
    totalDeviation: totalPct - totalTarget,
  };
}
