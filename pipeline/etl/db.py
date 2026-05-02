"""SQLite schema and connection helpers for the timemachine database."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

from .types import AllocationRow

# ── Schema DDL ───────────────────────────────────────────────────────────────

_TABLES = """
-- Fidelity transaction rows (from merged CSVs)
-- ``account_number`` is replay's grouping key; ``action`` is the raw CSV
-- string retained for audit/debugging; ``action_kind`` is the normalized enum
-- populated at ingest; ``lot_type`` is read by replay's lot-type bookkeeping.
-- ``action_type`` is the coarse (deposit/buy/sell/...) classification exposed
-- to the frontend API artifacts.
CREATE TABLE IF NOT EXISTS fidelity_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date        TEXT NOT NULL,
    -- account_number + action default to '' so legacy local DBs can add the
    -- columns without rejecting pre-existing rows; real ingest always
    -- populates both from the Fidelity CSV.
    account_number  TEXT NOT NULL DEFAULT '',
    action          TEXT NOT NULL DEFAULT '',
    action_type     TEXT NOT NULL DEFAULT '',
    action_kind     TEXT,
    symbol          TEXT NOT NULL DEFAULT '',
    lot_type        TEXT NOT NULL DEFAULT '',
    quantity        REAL NOT NULL DEFAULT 0,
    price           REAL NOT NULL DEFAULT 0,
    amount          REAL NOT NULL DEFAULT 0
);

-- Robinhood transaction rows (from activity report CSV).
-- Schema mirrors fidelity_transactions' primitive-native columns (txn_date,
-- action_kind, ticker, quantity, amount_usd) so the source-agnostic
-- replay_transactions primitive in etl/replay.py can replay this table
-- without any column aliasing.
--
-- Idempotency note: we intentionally do NOT add a UNIQUE(txn_date, ticker,
-- action, quantity, amount_usd) constraint. Robinhood CSVs legitimately
-- contain duplicate rows (e.g. two recurring buys of identical lot/price on
-- the same day → 2 physical shares bought, not 1), and :mod:`etl.sources.fidelity`
-- documents this explicitly: "preserving both matches reality better than
-- collapsing them." Idempotent re-ingest is instead guaranteed by the
-- range-replace pattern in :func:`etl.sources.robinhood.ingest`
-- (DELETE within the CSV's [min_date, max_date] + INSERT everything).
CREATE TABLE IF NOT EXISTS robinhood_transactions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    txn_date        TEXT NOT NULL,
    action          TEXT NOT NULL DEFAULT '',  -- raw Trans Code from CSV (Buy/Sell/CDIV/...)
    action_kind     TEXT NOT NULL,             -- normalized ActionKind enum (buy/sell/...)
    ticker          TEXT NOT NULL DEFAULT '',
    quantity        REAL NOT NULL DEFAULT 0,
    amount_usd      REAL NOT NULL DEFAULT 0,
    raw_description TEXT NOT NULL DEFAULT ''
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

-- Qianji transaction rows (from Qianji app DB)
-- ``note`` is read by changelog.py for the low-count-category row expansion
-- in the daily sync email; ``is_retirement`` lets the frontend split income
-- into retirement vs take-home without substring sniffing. ``account_to``
-- exposes Qianji's ``targetact`` so the cross-check can match Fidelity
-- deposits against income entries booked directly into a Fidelity account
-- (payroll direct deposits, rebate rewards) — not just transfers.
CREATE TABLE IF NOT EXISTS qianji_transactions (
    date           TEXT NOT NULL,
    type           TEXT NOT NULL,
    category       TEXT NOT NULL DEFAULT '',
    amount         REAL NOT NULL,
    note           TEXT NOT NULL DEFAULT '',
    is_retirement  INTEGER NOT NULL DEFAULT 0,
    account_to     TEXT NOT NULL DEFAULT ''
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

-- Category metadata populated from config.json's target_weights +
-- category_order. The R2 exporter exposes this as camelCase JSON so the
-- allocation palette/targets have a single source of truth.
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
CREATE INDEX IF NOT EXISTS idx_robinhood_date    ON robinhood_transactions(txn_date);
CREATE INDEX IF NOT EXISTS idx_daily_close_date  ON daily_close(date);
CREATE INDEX IF NOT EXISTS idx_daily_tickers_date ON computed_daily_tickers(date);
CREATE INDEX IF NOT EXISTS idx_qianji_txn_date ON qianji_transactions(date);
CREATE INDEX IF NOT EXISTS idx_econ_series_key ON econ_series(key);
"""


# ── Public API ───────────────────────────────────────────────────────────────


def init_db(path: Path) -> None:
    """Create the timemachine SQLite database with all tables and indexes."""
    conn = sqlite3.connect(path)
    conn.executescript(_TABLES)
    conn.executescript(_INDEXES)
    conn.commit()
    conn.close()


def get_connection(path: Path) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_readonly_connection(path: Path) -> sqlite3.Connection:
    """Return a read-only connection via SQLite's URI mode.

    Used by reporters (changelog snapshot, allocation's Qianji date read,
    Qianji record loader) that only ever SELECT — protects against a code-
    path bug accidentally mutating the DB. Does not enforce the
    ``file.exists()`` check; callers typically gate on that separately so
    the "DB doesn't exist yet" case is an empty-result fast path rather
    than an exception.
    """
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


# ── Incremental build helpers for computed_daily ───────────────────────────


def get_last_computed_date(db_path: Path) -> date | None:
    """Return the latest date in computed_daily, or None if empty."""
    conn = get_connection(db_path)
    try:
        row = conn.execute("SELECT MAX(date) FROM computed_daily").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None
    finally:
        conn.close()


def upsert_daily_rows(db_path: Path, rows: list[AllocationRow]) -> int:
    """Upsert rows into computed_daily + computed_daily_tickers.

    Overwrites existing rows for the same date. Incremental builds recompute
    the last REFRESH_WINDOW_DAYS days to pick up intraday price updates and
    late Yahoo corrections, so duplicate dates must replace, not skip.
    Child tickers for each replaced date are wiped first so a removed holding
    doesn't leave an orphan row. Returns number of rows written.
    """
    if not rows:
        return 0

    conn = get_connection(db_path)
    try:
        for r in rows:
            conn.execute("DELETE FROM computed_daily_tickers WHERE date = ?", (r["date"],))
            conn.execute(
                "INSERT OR REPLACE INTO computed_daily"
                " (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["date"], r["total"], r["us_equity"], r["non_us_equity"],
                 r["crypto"], r["safe_net"], r["liabilities"]),
            )
            for t in r["tickers"]:
                conn.execute(
                    "INSERT OR REPLACE INTO computed_daily_tickers"
                    " (date, ticker, value, category, subtype, cost_basis, gain_loss, gain_loss_pct)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (r["date"], t["ticker"], t["value"], t["category"], t["subtype"],
                     t["cost_basis"], t["gain_loss"], t["gain_loss_pct"]),
                )
        conn.commit()
        return len(rows)
    finally:
        conn.close()
