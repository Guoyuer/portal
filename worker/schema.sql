-- D1 schema for portal-api Worker
-- Source of truth for table definitions queried by the Worker.
-- Views expose camelCase column names matching the TypeScript type contract.

-- ── Tables ────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS computed_daily (
  date TEXT PRIMARY KEY,
  total REAL,
  us_equity REAL,
  non_us_equity REAL,
  crypto REAL,
  safe_net REAL,
  liabilities REAL
);

CREATE TABLE IF NOT EXISTS computed_prefix (
  date TEXT PRIMARY KEY,
  income REAL,
  expenses REAL,
  buys REAL,
  sells REAL,
  dividends REAL,
  net_cash_in REAL,
  cc_payments REAL
);

CREATE TABLE IF NOT EXISTS computed_daily_tickers (
  date TEXT NOT NULL,
  ticker TEXT NOT NULL,
  value REAL,
  category TEXT,
  subtype TEXT,
  cost_basis REAL,
  gain_loss REAL,
  gain_loss_pct REAL,
  PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS fidelity_transactions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_date TEXT,
  action_type TEXT,
  symbol TEXT,
  amount REAL
);

CREATE TABLE IF NOT EXISTS qianji_transactions (
  date TEXT,
  type TEXT,
  category TEXT,
  amount REAL
);

CREATE TABLE IF NOT EXISTS computed_market_indices (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  current REAL,
  month_return REAL,
  ytd_return REAL,
  high_52w REAL,
  low_52w REAL,
  sparkline TEXT
);

CREATE TABLE IF NOT EXISTS computed_market_indicators (
  key TEXT PRIMARY KEY,
  value REAL
);

CREATE TABLE IF NOT EXISTS computed_holdings_detail (
  ticker TEXT PRIMARY KEY,
  month_return REAL,
  start_value REAL,
  end_value REAL,
  high_52w REAL,
  low_52w REAL,
  vs_high REAL
);

-- ── Indexes ───────────────────────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_daily_tickers_date ON computed_daily_tickers(date);
CREATE INDEX IF NOT EXISTS idx_fidelity_date ON fidelity_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_qianji_txn_date ON qianji_transactions(date);

-- ── camelCase views (match TypeScript type contract) ──────────────────────────

CREATE VIEW IF NOT EXISTS v_daily AS
SELECT date, total, us_equity AS usEquity, non_us_equity AS nonUsEquity,
  crypto, safe_net AS safeNet, liabilities
FROM computed_daily ORDER BY date;

CREATE VIEW IF NOT EXISTS v_prefix AS
SELECT date, income, expenses, buys, sells, dividends,
  net_cash_in AS netCashIn, cc_payments AS ccPayments
FROM computed_prefix ORDER BY date;

CREATE VIEW IF NOT EXISTS v_daily_tickers AS
SELECT date, ticker, value, category, subtype,
  cost_basis AS costBasis, gain_loss AS gainLoss, gain_loss_pct AS gainLossPct
FROM computed_daily_tickers ORDER BY date, value DESC;

CREATE VIEW IF NOT EXISTS v_fidelity_txns AS
SELECT run_date AS runDate, action_type AS actionType, symbol, amount
FROM fidelity_transactions ORDER BY id;

CREATE VIEW IF NOT EXISTS v_qianji_txns AS
SELECT date, type, category, amount
FROM qianji_transactions ORDER BY date;

CREATE VIEW IF NOT EXISTS v_market_indices AS
SELECT ticker, name, current, month_return AS monthReturn,
  ytd_return AS ytdReturn, high_52w AS high52w, low_52w AS low52w, sparkline
FROM computed_market_indices ORDER BY ticker;

CREATE VIEW IF NOT EXISTS v_market_indicators AS
SELECT key, value FROM computed_market_indicators;

CREATE VIEW IF NOT EXISTS v_holdings_detail AS
SELECT ticker, month_return AS monthReturn, start_value AS startValue,
  end_value AS endValue, high_52w AS high52w, low_52w AS low52w, vs_high AS vsHigh
FROM computed_holdings_detail ORDER BY month_return DESC;
