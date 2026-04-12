"""SQLite schema and connection helpers for the timemachine database."""

from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path
from typing import Any

from .empower_401k import Contribution, parse_qfx
from .ingest.fidelity_history import _classify_action
from .types import parse_float as _parse_float

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
    date     TEXT NOT NULL,
    type     TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT '',
    amount   REAL NOT NULL,
    account  TEXT NOT NULL DEFAULT '',
    note     TEXT NOT NULL DEFAULT ''
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
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_fidelity_date     ON fidelity_transactions(run_date);
CREATE INDEX IF NOT EXISTS idx_fidelity_acct_sym ON fidelity_transactions(account_number, symbol);
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
    conn.close()


def get_connection(path: Path) -> sqlite3.Connection:
    """Return a connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Fidelity CSV ingestion ──────────────────────────────────────────────────

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _mmddyyyy_to_sort(date_str: str) -> str:
    """Convert MM/DD/YYYY to YYYYMMDD for date range comparison."""
    parts = date_str.strip().split("/")
    return f"{parts[2]}{parts[0]}{parts[1]}"


def ingest_fidelity_csv(db_path: Path, csv_path: Path) -> int:
    """Ingest a Fidelity CSV into the database, replacing overlapping date ranges.

    Returns the total row count in fidelity_transactions after ingestion.
    """
    # Read CSV, handling BOM and leading blank lines
    text = csv_path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()

    # Find the header line (starts with "Run Date")
    header_idx = -1
    for i, line in enumerate(lines):
        if line.strip().startswith("Run Date"):
            header_idx = i
            break
    if header_idx == -1:
        msg = f"No header row found in {csv_path}"
        raise ValueError(msg)

    # Parse with csv.DictReader from the header line onward
    reader = csv.DictReader(lines[header_idx:])
    rows: list[tuple[str, str, str, str, str, str, str, str, float, float, float, str]] = []
    dates: list[str] = []

    for record in reader:
        run_date = record.get("Run Date", "").strip()
        if not _DATE_RE.match(run_date):
            continue

        raw_action = record.get("Action", "").strip().strip('"')
        rows.append((
            run_date,
            record.get("Account", "").strip().strip('"'),
            record.get("Account Number", "").strip().strip('"'),
            raw_action,
            _classify_action(raw_action),
            record.get("Symbol", "").strip(),
            record.get("Description", "").strip().strip('"'),
            record.get("Type", "").strip(),
            _parse_float(record.get("Quantity", "")),
            _parse_float(record.get("Price", "")),
            _parse_float(record.get("Amount", "")),
            record.get("Settlement Date", "").strip(),
        ))
        dates.append(run_date)

    if not rows:
        conn = get_connection(db_path)
        count: int = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
        conn.close()
        return count

    # Date range for overlap deletion (YYYYMMDD format for comparison)
    sort_dates = [_mmddyyyy_to_sort(d) for d in dates]
    min_date = min(sort_dates)
    max_date = max(sort_dates)

    conn = get_connection(db_path)
    try:
        # Delete existing rows in the date range of this file
        conn.execute(
            """DELETE FROM fidelity_transactions
               WHERE substr(run_date,7,4) || substr(run_date,1,2) || substr(run_date,4,2)
                     BETWEEN ? AND ?""",
            (min_date, max_date),
        )

        # Insert all new rows
        conn.executemany(
            """INSERT INTO fidelity_transactions
               (run_date, account, account_number, action, action_type, symbol,
                description, lot_type, quantity, price, amount, settlement_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows,
        )
        conn.commit()

        count = conn.execute("SELECT COUNT(*) FROM fidelity_transactions").fetchone()[0]
    finally:
        conn.close()

    return count


# ── Qianji transaction ingestion ──────────────────────────────────────────


def ingest_qianji_transactions(db_path: Path, records: list[dict[str, Any]]) -> int:
    """Ingest Qianji transaction records into the database.

    Clears and replaces all rows. Returns row count.
    """
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM qianji_transactions")
        if records:
            conn.executemany(
                "INSERT INTO qianji_transactions (date, type, category, amount, account, note)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (
                        r["date"][:10],  # truncate datetime to date
                        r["type"],
                        r.get("category", ""),
                        r["amount"],
                        r.get("account_from", ""),
                        r.get("note", ""),
                    )
                    for r in records
                ],
            )
        conn.commit()
        count: int = conn.execute("SELECT COUNT(*) FROM qianji_transactions").fetchone()[0]
    finally:
        conn.close()
    return count


# ── Empower QFX ingestion ─────────────────────────────────────────────────


def ingest_empower_qfx(db_path: Path, qfx_path: Path) -> int:
    """Ingest an Empower 401k QFX file into the database.

    Upserts the snapshot by date; replaces all fund positions for that snapshot.
    Returns the number of funds inserted.
    """
    snap = parse_qfx(qfx_path)
    if not snap.funds:
        return 0

    conn = get_connection(db_path)
    try:
        snap_date = snap.date.isoformat()
        conn.execute("INSERT OR IGNORE INTO empower_snapshots (snapshot_date) VALUES (?)", (snap_date,))
        row = conn.execute("SELECT id FROM empower_snapshots WHERE snapshot_date = ?", (snap_date,)).fetchone()
        snapshot_id: int = row[0]

        conn.execute("DELETE FROM empower_funds WHERE snapshot_id = ?", (snapshot_id,))
        conn.executemany(
            "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval) VALUES (?, ?, ?, ?, ?, ?)",
            [(snapshot_id, f.cusip, f.ticker, f.shares, f.price, f.mktval) for f in snap.funds],
        )
        conn.commit()
    finally:
        conn.close()

    return len(snap.funds)


# ── Empower contributions ingestion ─────────────────────────────────────────


def ingest_empower_contributions(db_path: Path, contributions: list[Contribution]) -> int:
    """Upsert 401k contributions (BUYMF) into the database.

    Deduplicates on (date, amount, ticker, cusip).
    Returns number of rows after ingestion.
    """
    if not contributions:
        return 0

    conn = get_connection(db_path)
    try:
        conn.executemany(
            "INSERT OR REPLACE INTO empower_contributions (date, amount, ticker, cusip)"
            " VALUES (?, ?, ?, ?)",
            [
                (c.date.isoformat(), c.amount, c.ticker, getattr(c, "cusip", ""))
                for c in contributions
            ],
        )
        conn.commit()
        count: int = conn.execute("SELECT COUNT(*) FROM empower_contributions").fetchone()[0]
    finally:
        conn.close()
    return count


# ── Price ingestion ────────────────────────────────────────────────────────


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
