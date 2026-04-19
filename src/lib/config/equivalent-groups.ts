// ── Economically-equivalent ticker groups ───────────────────────────────
// Hand-maintained. A ticker must appear in at most one group; the
// invariant check throws at module-load if violated, so a bad edit
// breaks the build instead of silently mis-classifying transactions.

export type EquivalentGroup = {
  key: string;
  display: string;
  tickers: string[];
};

export const EQUIVALENT_GROUPS: Record<string, EquivalentGroup> = {
  nasdaq_100: {
    key: "nasdaq_100",
    display: "NASDAQ 100",
    tickers: ["QQQ", "QQQM", "401k tech"],
  },
  sp500: {
    key: "sp500",
    display: "S&P 500",
    tickers: ["VOO", "IVV", "SPY", "FXAIX", "401k sp500"],
  },
};

function buildIndex(): Map<string, string> {
  const m = new Map<string, string>();
  for (const [key, group] of Object.entries(EQUIVALENT_GROUPS)) {
    for (const t of group.tickers) {
      const existing = m.get(t);
      if (existing) {
        throw new Error(
          `Ticker "${t}" appears in both "${existing}" and "${key}" — equivalence groups must be disjoint`,
        );
      }
      m.set(t, key);
    }
  }
  return m;
}

export const GROUP_BY_TICKER: ReadonlyMap<string, string> = buildIndex();

export function groupOfTicker(ticker: string): string | null {
  return GROUP_BY_TICKER.get(ticker) ?? null;
}
