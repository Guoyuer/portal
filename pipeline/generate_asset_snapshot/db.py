"""SQLite schema and connection helpers for the timemachine database."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .types import MARKET_META_KEYS

# ── Schema DDL ───────────────────────────────────────────────────────────────

_TABLES = """
-- Fidelity transaction rows (from merged CSVs)
CREATE TABLE IF NOT EXISTS fidelity_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    account         TEXT NOT NULL,
    account_number  TEXT NOT NULL,
    action          TEXT NOT NULL,
    action_type     TEXT NOT NULL DEFAULT '',
    symbol          TEXT NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    lot_type        TEXT NOT NULL DEFAULT '',
    quantity        REAL NOT NULL DEFAULT 0,
    price           REAL NOT NULL DEFAULT 0,
    amount          REAL NOT NULL DEFAULT 0,
    settlement_date TEXT NOT NULL DEFAULT ''
);

-- Daily close prices + CNY rates (symbol='CNY=X' for rates)
CREATE TABLE IF NOT EXISTS daily_close (
    symbol TEXT NOT NULL,
    date   TEXT NOT NULL,
    close  REAL NOT NULL,
    PRIMARY KEY (symbol, date)
);

-- Empower 401k quarterly snapshots
CREATE TABLE IF NOT EXISTS empower_snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_date TEXT NOT NULL UNIQUE
);

-- Per-fund positions within a snapshot
CREATE TABLE IF NOT EXISTS empower_funds (
    snapshot_id INTEGER NOT NULL REFERENCES empower_snapshots(id),
    cusip       TEXT NOT NULL,
    ticker      TEXT NOT NULL,
    shares      REAL NOT NULL,
    price       REAL NOT NULL,
    mktval      REAL NOT NULL,
    PRIMARY KEY (snapshot_id, cusip)
);

-- Qianji account balances
CREATE TABLE IF NOT EXISTS qianji_balances (
    account  TEXT PRIMARY KEY,
    balance  REAL NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD'
);

-- Qianji transaction rows (from Qianji app DB)
CREATE TABLE IF NOT EXISTS qianji_transactions (
    date           TEXT NOT NULL,
    type           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',
    amount         REAL NOT NULL,
    account        TEXT NOT NULL DEFAULT '',
    note           TEXT NOT NULL DEFAULT '',
    is_retirement  INTEGER NOT NULL DEFAULT 0
);

-- Pre-computed daily point-in-time values
CREATE TABLE IF NOT EXISTS computed_daily (
    date          TEXT PRIMARY KEY,
    total         REAL NOT NULL,
    us_equity     REAL NOT NULL,
    non_us_equity REAL NOT NULL,
    crypto        REAL NOT NULL,
    safe_net      REAL NOT NULL,
    liabilities   REAL NOT NULL DEFAULT 0
);

-- Pre-computed daily ticker-level values
CREATE TABLE IF NOT EXISTS computed_daily_tickers (
    date          TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    value         REAL NOT NULL,
    category      TEXT NOT NULL DEFAULT '',
    subtype       TEXT NOT NULL DEFAULT '',
    cost_basis    REAL NOT NULL DEFAULT 0,
    gain_loss     REAL NOT NULL DEFAULT 0,
    gain_loss_pct REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, ticker)
);

-- Empower 401k contributions (BUYMF transactions from QFX)
CREATE TABLE IF NOT EXISTS empower_contributions (
    date   TEXT NOT NULL,
    amount REAL NOT NULL,
    ticker TEXT NOT NULL,
    cusip  TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (date, amount, ticker, cusip)
);

-- Pre-computed market index data (^GSPC, ^NDX, etc.)
CREATE TABLE IF NOT EXISTS computed_market_indices (
    ticker       TEXT PRIMARY KEY,
    name         TEXT NOT NULL DEFAULT '',
    current      REAL NOT NULL DEFAULT 0,
    month_return REAL NOT NULL DEFAULT 0,
    ytd_return   REAL NOT NULL DEFAULT 0,
    high_52w     REAL NOT NULL DEFAULT 0,
    low_52w      REAL NOT NULL DEFAULT 0,
    sparkline    TEXT NOT NULL DEFAULT '[]'
);

-- Pre-computed macro scalar indicators (fedRate, usdCny, etc.)
CREATE TABLE IF NOT EXISTS computed_market_indicators (
    key   TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0
);

-- Pre-computed per-ticker holdings performance
CREATE TABLE IF NOT EXISTS computed_holdings_detail (
    ticker       TEXT PRIMARY KEY,
    month_return REAL NOT NULL DEFAULT 0,
    start_value  REAL NOT NULL DEFAULT 0,
    end_value    REAL NOT NULL DEFAULT 0,
    high_52w     REAL,
    low_52w      REAL,
    vs_high      REAL
);

-- FRED economic time-series (monthly, 5yr lookback)
CREATE TABLE IF NOT EXISTS econ_series (
    key   TEXT NOT NULL,
    date  TEXT NOT NULL,
    value REAL NOT NULL,
    PRIMARY KEY (key, date)
);

-- Replay checkpoint: cached positions/cash/cost_basis at a point in time
CREATE TABLE IF NOT EXISTS replay_checkpoint (
    date       TEXT PRIMARY KEY,
    positions  TEXT NOT NULL,
    cash       TEXT NOT NULL,
    cost_basis TEXT NOT NULL
);

-- Calibration log: records drift between replay and positions CSV
CREATE TABLE IF NOT EXISTS calibration_log (
    date              TEXT PRIMARY KEY,
    days_since_last   INTEGER,
    total_cb_drift    REAL NOT NULL DEFAULT 0,
    total_cb_pct      REAL NOT NULL DEFAULT 0,
    positions_ok      INTEGER NOT NULL DEFAULT 0,
    positions_total   INTEGER NOT NULL DEFAULT 0,
    details           TEXT NOT NULL DEFAULT '[]'
);

-- Category metadata populated from config.json's target_weights +
-- category_order. The frontend reads this via v_categories so the allocation
-- palette/targets have a single source of truth.
CREATE TABLE IF NOT EXISTS categories (
    key           TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    display_order INTEGER NOT NULL,
    target_pct    REAL NOT NULL DEFAULT 0
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_fidelity_date     ON fidelity_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_fidelity_acct_sym ON fidelity_transactions(account_number, symbol);
CREATE INDEX IF NOT EXISTS idx_daily_close_date  ON daily_close(date);
CREATE INDEX IF NOT EXISTS idx_daily_tickers_date ON computed_daily_tickers(date);
CREATE INDEX IF NOT EXISTS idx_qianji_txn_date ON qianji_transactions(date);
CREATE INDEX IF NOT EXISTS idx_econ_series_key ON econ_series(key);
"""


# ── Views (camelCase API contract) ──────────────────────────────────────────


def _build_v_market_meta_sql() -> str:
    """Pivot computed_market_indicators (key, value) → one wide row.

    The column list is derived from MARKET_META_KEYS so adding a FRED indicator
    requires editing only that list (and emitting the row in precompute.py).
    """
    cols = ",\n  ".join(
        f"MAX(CASE WHEN key = '{k}' THEN value END) AS {k}" for k in MARKET_META_KEYS
    )
    return (
        "CREATE VIEW IF NOT EXISTS v_market_meta AS\n"
        "SELECT\n"
        f"  {cols}\n"
        "FROM computed_market_indicators;"
    )


_VIEWS: dict[str, str] = {
    "v_daily": (
        "CREATE VIEW IF NOT EXISTS v_daily AS\n"
        "SELECT date, total, us_equity AS usEquity, non_us_equity AS nonUsEquity,\n"
        "  crypto, safe_net AS safeNet, liabilities\n"
        "FROM computed_daily ORDER BY date;"
    ),
    "v_daily_tickers": (
        "CREATE VIEW IF NOT EXISTS v_daily_tickers AS\n"
        "SELECT date, ticker, value, category, subtype,\n"
        "  cost_basis AS costBasis, gain_loss AS gainLoss, gain_loss_pct AS gainLossPct\n"
        "FROM computed_daily_tickers ORDER BY date, value DESC;"
    ),
    "v_fidelity_txns": (
        "CREATE VIEW IF NOT EXISTS v_fidelity_txns AS\n"
        "SELECT run_date AS runDate, action_type AS actionType, symbol, amount,\n"
        "  quantity, price\n"
        "FROM fidelity_transactions ORDER BY id;"
    ),
    "v_qianji_txns": (
        "CREATE VIEW IF NOT EXISTS v_qianji_txns AS\n"
        "SELECT date, type, category, amount,\n"
        "  is_retirement AS isRetirement\n"
        "FROM qianji_transactions ORDER BY date;"
    ),
    "v_market_indices": (
        "CREATE VIEW IF NOT EXISTS v_market_indices AS\n"
        "SELECT ticker, name, current, month_return AS monthReturn,\n"
        "  ytd_return AS ytdReturn, high_52w AS high52w, low_52w AS low52w, sparkline\n"
        "FROM computed_market_indices ORDER BY ticker;"
    ),
    "v_market_indicators": (
        "CREATE VIEW IF NOT EXISTS v_market_indicators AS\n"
        "SELECT key, value FROM computed_market_indicators;"
    ),
    "v_market_meta": _build_v_market_meta_sql(),
    "v_holdings_detail": (
        "CREATE VIEW IF NOT EXISTS v_holdings_detail AS\n"
        "SELECT ticker, month_return AS monthReturn, start_value AS startValue,\n"
        "  end_value AS endValue, high_52w AS high52w, low_52w AS low52w, vs_high AS vsHigh\n"
        "FROM computed_holdings_detail ORDER BY month_return DESC;"
    ),
    "v_econ_series": (
        "CREATE VIEW IF NOT EXISTS v_econ_series AS\n"
        "SELECT key, date, value FROM econ_series ORDER BY key, date;"
    ),
    # Pre-grouped for the Worker /econ endpoint — each row is a key plus a
    # JSON array of {date, value} already built by SQLite. The client parses
    # the string via the EconDataSchema transform.
    "v_econ_series_grouped": (
        "CREATE VIEW IF NOT EXISTS v_econ_series_grouped AS\n"
        "SELECT key,\n"
        "  json_group_array(json_object('date', date, 'value', value)) AS points\n"
        "FROM (SELECT key, date, value FROM econ_series ORDER BY key, date)\n"
        "GROUP BY key ORDER BY key;"
    ),
    "v_econ_snapshot": (
        "CREATE VIEW IF NOT EXISTS v_econ_snapshot AS\n"
        "SELECT key, value\n"
        "FROM econ_series t1\n"
        "WHERE date = (SELECT MAX(date) FROM econ_series t2 WHERE t2.key = t1.key);"
    ),
    "v_categories": (
        "CREATE VIEW IF NOT EXISTS v_categories AS\n"
        "SELECT key, name,\n"
        "  display_order AS displayOrder,\n"
        "  target_pct AS targetPct\n"
        "FROM categories ORDER BY display_order;"
    ),
}


# ── Public API ───────────────────────────────────────────────────────────────


def init_db(path: Path) -> None:
    """Create the timemachine SQLite database with all tables, indexes, and views."""
    conn = sqlite3.connect(path)
    conn.executescript(_TABLES)
    conn.executescript(_INDEXES)
    for view_sql in _VIEWS.values():
        conn.execute(view_sql)
    conn.close()


def get_connection(path: Path) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Econ series ingestion ──────────────────────────────────────────────────


def ingest_econ_series(path: Path, series: dict[str, list[dict[str, Any]]]) -> int:
    """Write FRED time-series to econ_series table. Returns row count."""
    conn = get_connection(path)
    try:
        conn.execute("DELETE FROM econ_series")
        count = 0
        for key, points in series.items():
            for pt in points:
                conn.execute(
                    "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                    (key, pt["date"], pt["value"]),
                )
                count += 1
        conn.commit()
        return count
    finally:
        conn.close()


# ── Price ingestion ────────────────────────────────────────────────────────


def ingest_prices(db_path: Path, prices: dict[str, dict[str, float]]) -> None:
    """Ingest daily close prices into the database.

    Args:
        db_path: Path to the SQLite database.
        prices: ``{"VOO": {"2025-01-02": 500.0, ...}, ...}``
    """
    rows: list[tuple[str, str, float]] = []
    for symbol, date_prices in prices.items():
        for dt, close in date_prices.items():
            rows.append((symbol, dt, close))

    if not rows:
        return

    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()
