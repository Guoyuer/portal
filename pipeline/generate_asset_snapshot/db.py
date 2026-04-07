"""SQLite schema and connection helpers for the timemachine database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

# ── Schema DDL ───────────────────────────────────────────────────────────────

_TABLES = """
-- Fidelity transaction rows (from merged CSVs)
CREATE TABLE IF NOT EXISTS fidelity_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    account         TEXT NOT NULL,
    account_number  TEXT NOT NULL,
    action          TEXT NOT NULL,
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

-- Pre-computed daily point-in-time values
CREATE TABLE IF NOT EXISTS computed_daily (
    date          TEXT PRIMARY KEY,
    total         REAL NOT NULL,
    us_equity     REAL NOT NULL,
    non_us_equity REAL NOT NULL,
    crypto        REAL NOT NULL,
    safe_net      REAL NOT NULL
);

-- Pre-computed prefix sums
CREATE TABLE IF NOT EXISTS computed_prefix (
    date        TEXT PRIMARY KEY,
    income      REAL NOT NULL DEFAULT 0,
    expenses    REAL NOT NULL DEFAULT 0,
    buys        REAL NOT NULL DEFAULT 0,
    sells       REAL NOT NULL DEFAULT 0,
    dividends   REAL NOT NULL DEFAULT 0,
    net_cash_in REAL NOT NULL DEFAULT 0,
    cc_payments REAL NOT NULL DEFAULT 0
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_fidelity_date     ON fidelity_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_fidelity_acct_sym ON fidelity_transactions(account_number, symbol);
CREATE INDEX IF NOT EXISTS idx_daily_close_date  ON daily_close(date);
"""

# ── Public API ───────────────────────────────────────────────────────────────


def init_db(path: Path) -> None:
    """Create the timemachine SQLite database with all tables and indexes."""
    conn = sqlite3.connect(path)
    conn.executescript(_TABLES)
    conn.executescript(_INDEXES)
    conn.close()


def get_connection(path: Path) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
