// ── Mock API server for E2E tests ────────────────────────────────────────
// Serves minimal fixture data on /timeline and /econ so E2E tests don't
// depend on the production Worker. Started by Playwright via webServer.

import http from "node:http";

// ── Fixture data ─────────────────────────────────────────────────────────

function generateDaily(days: number) {
  const daily = [];
  const base = new Date("2024-01-02");
  for (let i = 0; i < days; i++) {
    const d = new Date(base);
    d.setDate(d.getDate() + i);
    // Skip weekends
    if (d.getDay() === 0 || d.getDay() === 6) continue;
    const t = 80000 + i * 500 + Math.sin(i / 30) * 10000;
    daily.push({
      date: d.toISOString().slice(0, 10),
      total: Math.round(t),
      usEquity: Math.round(t * 0.55),
      nonUsEquity: Math.round(t * 0.15),
      crypto: Math.round(t * 0.03),
      safeNet: Math.round(t * 0.27),
      liabilities: -2000,
    });
  }
  return daily;
}

const daily = generateDaily(800);
const lastDate = daily[daily.length - 1].date;

const TIMELINE = {
  daily,
  dailyTickers: daily.flatMap((d) => [
    { date: d.date, ticker: "VOO", value: d.usEquity * 0.5, category: "US Equity", subtype: "broad", costBasis: d.usEquity * 0.35, gainLoss: d.usEquity * 0.15, gainLossPct: 43 },
    { date: d.date, ticker: "SCHG", value: d.usEquity * 0.3, category: "US Equity", subtype: "growth", costBasis: d.usEquity * 0.2, gainLoss: d.usEquity * 0.1, gainLossPct: 50 },
    { date: d.date, ticker: "AAPL", value: d.usEquity * 0.2, category: "US Equity", subtype: "single stock", costBasis: d.usEquity * 0.15, gainLoss: d.usEquity * 0.05, gainLossPct: 33 },
    { date: d.date, ticker: "VXUS", value: d.nonUsEquity, category: "Non-US Equity", subtype: "broad", costBasis: d.nonUsEquity * 0.9, gainLoss: d.nonUsEquity * 0.1, gainLossPct: 11 },
    { date: d.date, ticker: "BTC", value: d.crypto, category: "Crypto", subtype: "digital asset", costBasis: d.crypto * 0.5, gainLoss: d.crypto * 0.5, gainLossPct: 100 },
    { date: d.date, ticker: "FZFXX", value: d.safeNet * 0.7, category: "Safe Net", subtype: "money market", costBasis: d.safeNet * 0.7, gainLoss: 0, gainLossPct: 0 },
    { date: d.date, ticker: "Chase", value: d.safeNet * 0.3, category: "Safe Net", subtype: "checking", costBasis: d.safeNet * 0.3, gainLoss: 0, gainLossPct: 0 },
  ]),
  fidelityTxns: [
    { runDate: "01/15/2024", actionType: "deposit", symbol: "", amount: 5000 },
    { runDate: "01/15/2024", actionType: "buy", symbol: "VOO", amount: -4000 },
    { runDate: "02/15/2024", actionType: "deposit", symbol: "", amount: 5000 },
    { runDate: "02/15/2024", actionType: "buy", symbol: "VOO", amount: -4000 },
    { runDate: "03/15/2024", actionType: "dividend", symbol: "VOO", amount: 150 },
    { runDate: "03/15/2024", actionType: "sell", symbol: "AAPL", amount: 2000 },
    // Recent transactions (within default brush range)
    { runDate: "01/15/2026", actionType: "deposit", symbol: "", amount: 5000 },
    { runDate: "01/15/2026", actionType: "buy", symbol: "VOO", amount: -4000 },
    { runDate: "02/15/2026", actionType: "buy", symbol: "SCHG", amount: -3000 },
    { runDate: "03/15/2026", actionType: "dividend", symbol: "VOO", amount: 200 },
  ],
  qianjiTxns: [
    { date: "2024-01-31", type: "income", category: "Salary", amount: 8000 },
    { date: "2024-01-31", type: "income", category: "401K", amount: 1600 },
    { date: "2024-01-15", type: "expense", category: "Rent", amount: 2200 },
    { date: "2024-01-20", type: "expense", category: "Meals", amount: 350 },
    { date: "2024-01-25", type: "expense", category: "Subscriptions", amount: 120 },
    { date: "2024-01-27", type: "transfer", category: "Other", amount: 5000 },
    { date: "2024-01-28", type: "repayment", category: "Other", amount: 500 },
    // Recent transactions (within default brush range)
    { date: "2026-01-31", type: "income", category: "Salary", amount: 8000 },
    { date: "2026-01-31", type: "income", category: "401K", amount: 1600 },
    { date: "2026-01-15", type: "expense", category: "Rent", amount: 2200 },
    { date: "2026-01-20", type: "expense", category: "Meals", amount: 350 },
    { date: "2026-02-28", type: "income", category: "Salary", amount: 8000 },
    { date: "2026-02-15", type: "expense", category: "Rent", amount: 2200 },
    { date: "2026-02-18", type: "expense", category: "Meals", amount: 280 },
    { date: "2026-03-31", type: "income", category: "Salary", amount: 8000 },
    { date: "2026-03-15", type: "expense", category: "Rent", amount: 2200 },
    { date: "2026-03-20", type: "expense", category: "Meals", amount: 310 },
    { date: "2026-01-27", type: "transfer", category: "Other", amount: 5000 },
  ],
  market: {
    indices: [
      { ticker: "^GSPC", name: "S&P 500", current: 5800, monthReturn: 2.1, ytdReturn: 12.5, sparkline: [5500, 5600, 5700, 5750, 5800], high52w: 5900, low52w: 4800 },
      { ticker: "^NDX", name: "Nasdaq 100", current: 20500, monthReturn: 3.2, ytdReturn: 18.1, sparkline: [19000, 19500, 20000, 20200, 20500], high52w: 21000, low52w: 16000 },
    ],
    fedRate: 4.33,
    treasury10y: 4.25,
    cpi: 3.0,
    unemployment: 3.8,
    vix: 15.2,
    dxy: 104.5,
    usdCny: 7.24,
  },
  holdingsDetail: [
    { ticker: "VOO", monthReturn: 2.5, startValue: 150000, endValue: 153750, high52w: 160000, low52w: 120000, vsHigh: -3.9 },
    { ticker: "VXUS", monthReturn: 1.8, startValue: 40000, endValue: 40720, high52w: 42000, low52w: 35000, vsHigh: -3.0 },
  ],
  syncMeta: { last_sync: new Date().toISOString(), last_date: lastDate },
};

const ECON = {
  generatedAt: new Date().toISOString(),
  snapshot: { fedFundsRate: 4.33, treasury10y: 4.25, treasury2y: 4.0, spread2s10s: 0.25, cpiYoy: 3.0, coreCpiYoy: 3.2, unemployment: 3.8, vix: 15.2, oilWti: 78.5 },
  series: {
    fedFundsRate: [{ date: "2024-01", value: 5.33 }, { date: "2024-06", value: 5.33 }, { date: "2025-01", value: 4.33 }],
    cpiYoy: [{ date: "2024-01", value: 3.1 }, { date: "2024-06", value: 3.0 }, { date: "2025-01", value: 2.8 }],
  },
};

// ── Server ───────────────────────────────────────────────────────────────

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
  "Content-Type": "application/json",
};

const server = http.createServer((req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, CORS);
    res.end();
    return;
  }

  const url = new URL(req.url ?? "/", `http://localhost`);

  if (url.pathname === "/timeline") {
    res.writeHead(200, CORS);
    res.end(JSON.stringify(TIMELINE));
    return;
  }

  if (url.pathname === "/econ") {
    res.writeHead(200, CORS);
    res.end(JSON.stringify(ECON));
    return;
  }

  res.writeHead(404, CORS);
  res.end(JSON.stringify({ error: "Not found" }));
});

const PORT = Number(process.env.MOCK_API_PORT ?? 4444);
server.listen(PORT, () => {
  console.log(`Mock API server listening on http://localhost:${PORT}`);
});
