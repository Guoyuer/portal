// ── Mock API server for E2E tests ────────────────────────────────────────
// Serves realistic fixture data on /timeline, /prices, and /econ
// so E2E tests don't depend on the production Worker.
// Started by Playwright via webServer.

import http from "node:http";

// ── Helpers ─────────────────────────────────────────────────────────────

function tradingDays(startIso: string, count: number): string[] {
  const dates: string[] = [];
  const d = new Date(startIso);
  while (dates.length < count) {
    if (d.getDay() !== 0 && d.getDay() !== 6) dates.push(d.toISOString().slice(0, 10));
    d.setDate(d.getDate() + 1);
  }
  return dates;
}

function seededRandom(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (Math.imul(1664525, state) + 1013904223) >>> 0;
    return state / 0x100000000;
  };
}

const rand = seededRandom(0x20260502);

/** Generate a realistic-looking price series with trend + noise. */
function priceSeries(dates: string[], base: number, drift: number, vol: number): { date: string; close: number }[] {
  let price = base;
  return dates.map((date, i) => {
    price += drift + Math.sin(i / 40) * vol + (rand() - 0.5) * vol * 0.3;
    if (price < 1) price = 1;
    return { date, close: Math.round(price * 100) / 100 };
  });
}

// ── Fixture dates ───────────────────────────────────────────────────────

const ALL_DATES = tradingDays("2024-01-02", 600);
const lastDate = ALL_DATES[ALL_DATES.length - 1];
const generatedAt = `${lastDate}T12:00:00.000Z`;

// ── Price histories per symbol ──────────────────────────────────────────

const SYMBOL_PRICES: Record<string, { date: string; close: number }[]> = {
  VOO:   priceSeries(ALL_DATES, 480, 0.15, 3),
  QQQM:  priceSeries(ALL_DATES, 175, 0.12, 2),
  VXUS:  priceSeries(ALL_DATES, 58,  0.05, 1.5),
  SCHD:  priceSeries(ALL_DATES, 28,  0.02, 0.5),
  AAPL:  priceSeries(ALL_DATES, 185, 0.08, 4),
  NVDA:  priceSeries(ALL_DATES, 500, 0.30, 10),
  GLDM:  priceSeries(ALL_DATES, 42,  0.03, 0.3),
  SGOV:  priceSeries(ALL_DATES, 100, 0.002, 0.05),
  FBTC:  priceSeries(ALL_DATES, 55,  0.10, 5),
  TSM:   priceSeries(ALL_DATES, 140, 0.10, 3),
};

/** Look up close on a date, fallback to base. */
function closeOn(sym: string, iso: string): number {
  const p = SYMBOL_PRICES[sym]?.find((p) => p.date === iso);
  return p?.close ?? 100;
}

// ── Daily totals ────────────────────────────────────────────────────────

const daily = ALL_DATES.map((date, i) => {
  const t = 80000 + i * 500 + Math.sin(i / 30) * 10000;
  return {
    date,
    total: Math.round(t),
    usEquity: Math.round(t * 0.55),
    nonUsEquity: Math.round(t * 0.15),
    crypto: Math.round(t * 0.03),
    safeNet: Math.round(t * 0.27),
    liabilities: -2000,
  };
});

// ── Daily tickers ───────────────────────────────────────────────────────

const dailyTickers = daily.flatMap((d) => [
  { date: d.date, ticker: "VOO",   value: d.usEquity * 0.35,  category: "US Equity",     subtype: "broad",        costBasis: d.usEquity * 0.25,  gainLoss: d.usEquity * 0.10, gainLossPct: 40 },
  { date: d.date, ticker: "QQQM",  value: d.usEquity * 0.20,  category: "US Equity",     subtype: "growth",       costBasis: d.usEquity * 0.14,  gainLoss: d.usEquity * 0.06, gainLossPct: 43 },
  { date: d.date, ticker: "AAPL",  value: d.usEquity * 0.10,  category: "US Equity",     subtype: "single stock", costBasis: d.usEquity * 0.07,  gainLoss: d.usEquity * 0.03, gainLossPct: 43 },
  { date: d.date, ticker: "NVDA",  value: d.usEquity * 0.08,  category: "US Equity",     subtype: "single stock", costBasis: d.usEquity * 0.04,  gainLoss: d.usEquity * 0.04, gainLossPct: 100 },
  { date: d.date, ticker: "SCHD",  value: d.usEquity * 0.07,  category: "US Equity",     subtype: "broad",        costBasis: d.usEquity * 0.06,  gainLoss: d.usEquity * 0.01, gainLossPct: 17 },
  { date: d.date, ticker: "VXUS",  value: d.nonUsEquity * 0.6, category: "Non-US Equity", subtype: "broad",        costBasis: d.nonUsEquity * 0.54, gainLoss: d.nonUsEquity * 0.06, gainLossPct: 11 },
  { date: d.date, ticker: "TSM",   value: d.nonUsEquity * 0.4, category: "Non-US Equity", subtype: "single stock", costBasis: d.nonUsEquity * 0.28, gainLoss: d.nonUsEquity * 0.12, gainLossPct: 43 },
  { date: d.date, ticker: "FBTC",  value: d.crypto,            category: "Crypto",        subtype: "digital asset", costBasis: d.crypto * 0.5,  gainLoss: d.crypto * 0.5, gainLossPct: 100 },
  { date: d.date, ticker: "SGOV",  value: d.safeNet * 0.30,   category: "Safe Net",      subtype: "treasury",     costBasis: d.safeNet * 0.30,   gainLoss: 0, gainLossPct: 0 },
  { date: d.date, ticker: "GLDM",  value: d.safeNet * 0.20,   category: "Safe Net",      subtype: "gold",         costBasis: d.safeNet * 0.18,   gainLoss: d.safeNet * 0.02, gainLossPct: 11 },
  { date: d.date, ticker: "SPAXX", value: d.safeNet * 0.30,   category: "Safe Net",      subtype: "money market", costBasis: d.safeNet * 0.30,   gainLoss: 0, gainLossPct: 0 },
  { date: d.date, ticker: "Chase", value: d.safeNet * 0.20,   category: "Safe Net",      subtype: "checking",     costBasis: d.safeNet * 0.20,   gainLoss: 0, gainLossPct: 0 },
]);

// ── Fidelity transactions (realistic DCA + sells + dividends) ───────────

type MockTxn = { runDate: string; actionType: string; symbol: string; amount: number; quantity: number; price: number };
const fidelityTxns: MockTxn[] = [];

function addFidelityTxn(runDate: string, actionType: string, symbol: string, amount: number, quantity = 0, price = 0): void {
  fidelityTxns.push({ runDate, actionType, symbol, amount, quantity, price });
}

function addPurchase(runDate: string, actionType: "buy" | "reinvestment", symbol: string, cash: number, price = closeOn(symbol, runDate)): void {
  addFidelityTxn(runDate, actionType, symbol, -cash, Math.round(cash / price * 1000) / 1000, price);
}

// Monthly DCA buys (bi-weekly for VOO, monthly for others)
for (let m = 0; m < 28; m++) {
  const d = new Date("2024-01-15");
  d.setMonth(d.getMonth() + m);
  if (d.getDay() === 0) d.setDate(d.getDate() + 1);
  if (d.getDay() === 6) d.setDate(d.getDate() + 2);
  const iso = d.toISOString().slice(0, 10);
  const rd = iso;

  // Deposits
  addFidelityTxn(rd, "deposit", "", 5000);

  // DCA buys
  const schdP = closeOn("SCHD", iso);
  const buys: Array<[string, number, number?]> = [
    ["VOO", 2000],
    ["QQQM", 1000],
    ["VXUS", 500],
    ["SCHD", 500, schdP],
  ];

  // Occasional single-stock buys
  if (m % 3 === 0) {
    buys.push(["NVDA", 800]);
  }
  if (m % 4 === 0) {
    buys.push(["TSM", 600]);
  }
  if (m % 6 === 0) {
    buys.push(["AAPL", 500]);
  }
  buys.push(["SGOV", 500], ["GLDM", 300]);
  for (const [symbol, cash, price] of buys) addPurchase(rd, "buy", symbol, cash, price);

  // Quarterly dividends
  if (m % 3 === 2) {
    addFidelityTxn(rd, "dividend", "VOO", 120);
    addFidelityTxn(rd, "dividend", "SCHD", 45);
    addPurchase(rd, "reinvestment", "SCHD", 45, schdP);
    addFidelityTxn(rd, "dividend", "VXUS", 30);
  }
  addPurchase(rd, "buy", "SPAXX", 50, 1);
}

// A few sells
addFidelityTxn("2025-03-15", "sell", "AAPL", 2000, -10, 200);
addFidelityTxn("2025-06-15", "sell", "NVDA", 3500, -5, 700);
addFidelityTxn("2025-09-15", "sell", "FBTC", 1200, -15, 80);

// ── Qianji transactions (realistic monthly pattern) ─────────────────────

type MockQianji = { date: string; type: string; category: string; amount: number; isRetirement?: boolean; accountTo?: string };
const qianjiTxns: MockQianji[] = [];

for (let m = 0; m < 28; m++) {
  const d = new Date("2024-01-01");
  d.setMonth(d.getMonth() + m);
  const ym = d.toISOString().slice(0, 7);

  // Income
  qianjiTxns.push({ date: `${ym}-28`, type: "income", category: "Salary", amount: 8000, accountTo: "" });
  qianjiTxns.push({ date: `${ym}-28`, type: "income", category: "401K", amount: 1600, isRetirement: true, accountTo: "" });

  // Fixed expenses
  qianjiTxns.push({ date: `${ym}-01`, type: "expense", category: "Rent", amount: 2200, accountTo: "" });
  qianjiTxns.push({ date: `${ym}-05`, type: "expense", category: "Subscriptions", amount: 65, accountTo: "" });

  // Variable expenses
  for (let w = 0; w < 4; w++) {
    qianjiTxns.push({ date: `${ym}-${String(w * 7 + 3).padStart(2, "0")}`, type: "expense", category: "Meals", amount: 60 + Math.round(rand() * 40), accountTo: "" });
    if (w % 2 === 0) qianjiTxns.push({ date: `${ym}-${String(w * 7 + 5).padStart(2, "0")}`, type: "expense", category: "Grocery", amount: 80 + Math.round(rand() * 60), accountTo: "" });
  }
  if (m % 2 === 0) qianjiTxns.push({ date: `${ym}-20`, type: "expense", category: "Travel", amount: 200 + Math.round(rand() * 300), accountTo: "" });
  if (m % 3 === 0) qianjiTxns.push({ date: `${ym}-15`, type: "expense", category: "Socializing", amount: 80 + Math.round(rand() * 120), accountTo: "" });

  // Transfer + repayment
  qianjiTxns.push({ date: `${ym}-10`, type: "transfer", category: "", amount: 5000, accountTo: "Fidelity taxable" });
  qianjiTxns.push({ date: `${ym}-25`, type: "repayment", category: "", amount: 500 + Math.round(rand() * 200), accountTo: "" });
}

// ── Market data ─────────────────────────────────────────────────────────

const market = {
  indices: [
    { ticker: "^GSPC", name: "S&P 500",    current: 5800, monthReturn: 2.1,  ytdReturn: 12.5, sparkline: [5500, 5550, 5600, 5650, 5700, 5750, 5800], high52w: 5900, low52w: 4800 },
    { ticker: "^NDX",  name: "Nasdaq 100",  current: 20500, monthReturn: 3.2, ytdReturn: 18.1, sparkline: [19000, 19200, 19500, 19800, 20000, 20200, 20500], high52w: 21000, low52w: 16000 },
    { ticker: "VXUS",  name: "FTSE All-World ex-US", current: 62, monthReturn: 1.8, ytdReturn: 5.2, sparkline: [58, 59, 60, 59, 61, 62, 62], high52w: 65, low52w: 52 },
    { ticker: "000300.SS", name: "CSI 300", current: 3900, monthReturn: -1.2, ytdReturn: -3.5, sparkline: [4100, 4050, 4000, 3950, 3900, 3950, 3900], high52w: 4200, low52w: 3500 },
  ],
};

// ── Category metadata (target weights + display order) ──────────────────

const categories = [
  { key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 55 },
  { key: "nonUsEquity", name: "Non-US Equity", displayOrder: 1, targetPct: 15 },
  { key: "crypto", name: "Crypto", displayOrder: 2, targetPct: 3 },
  { key: "safeNet", name: "Safe Net", displayOrder: 3, targetPct: 27 },
];

// ── Assembled timeline ──────────────────────────────────────────────────

const TIMELINE = {
  daily,
  dailyTickers,
  fidelityTxns,
  qianjiTxns: qianjiTxns.map((txn) => ({
    ...txn,
    isRetirement: txn.isRetirement ?? false,
    accountTo: txn.accountTo ?? "",
  })),
  robinhoodTxns: [],
  empowerContributions: [],
  categories,
  market,
  syncMeta: { last_sync: generatedAt, last_date: lastDate },
};

// ── Econ ─────────────────────────────────────────────────────────────────

const ECON = {
  generatedAt,
  snapshot: { fedFundsRate: 4.33, treasury10y: 4.25, treasury2y: 4.0, spread2s10s: 0.25, cpiYoy: 3.0, coreCpiYoy: 3.2, unemployment: 3.8, vix: 15.2, oilWti: 78.5 },
  series: {
    fedFundsRate: [{ date: "2024-01", value: 5.33 }, { date: "2024-06", value: 5.33 }, { date: "2025-01", value: 4.33 }],
    cpiYoy: [{ date: "2024-01", value: 3.1 }, { date: "2024-06", value: 3.0 }, { date: "2025-01", value: 2.8 }],
  },
};

// ── Server ───────────────────────────────────────────────────────────────

// Echo the request Origin so the mock behaves like a same-origin Worker route
// while still accepting cross-origin local dev requests.
function corsFor(req: http.IncomingMessage): Record<string, string> {
  // Node types `origin` as `string | string[] | undefined`. A comma-joined
  // array would be rejected by the browser; normalise to the first value.
  const rawOrigin = req.headers.origin;
  const origin = Array.isArray(rawOrigin) ? rawOrigin[0] : rawOrigin;
  return {
    "Access-Control-Allow-Origin": origin ?? "http://localhost:3100",
    "Access-Control-Allow-Credentials": "true",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
    "Content-Type": "application/json",
  };
}

const server = http.createServer((req, res) => {
  const CORS = corsFor(req);
  if (req.method === "OPTIONS") { res.writeHead(204, CORS); res.end(); return; }

  const url = new URL(req.url ?? "/", `http://localhost`);

  if (url.pathname === "/timeline") {
    res.writeHead(200, CORS);
    res.end(JSON.stringify(TIMELINE));
    return;
  }

  // /prices — bundle of all daily close prices + fidelity transactions by symbol
  if (url.pathname === "/prices") {
    const symbols = Array.from(new Set([...Object.keys(SYMBOL_PRICES), ...fidelityTxns.map((t) => t.symbol).filter(Boolean)]))
      .sort();
    const payload = Object.fromEntries(symbols.map((symbol) => {
      const prices = SYMBOL_PRICES[symbol] ?? [];
      const txns = fidelityTxns
        .filter((t) => t.symbol === symbol)
        .map((t) => ({ runDate: t.runDate, actionType: t.actionType, quantity: t.quantity, price: t.price, amount: t.amount }));
      return [symbol, { symbol, prices, transactions: txns }];
    }));
    res.writeHead(200, CORS);
    res.end(JSON.stringify(payload));
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

// `||` not `??`: an empty-string override should fall back rather than become
// Number("") === 0 and bind to an arbitrary free port. Matches the convention
// in src/lib/config.ts (env vars from CI/secrets can arrive as "" when the
// underlying secret is unset).
const PORT = Number(process.env.MOCK_API_PORT || 4444);
server.listen(PORT, () => {
  console.log(`Mock API server listening on http://localhost:${PORT}`);
});
