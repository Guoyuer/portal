"""Test-only helpers for seeding the timemachine SQLite DB.

Used by tests under ``pipeline/tests/``. Keeping these out of ``etl/db.py``
preserves the boundary between production code and test infrastructure.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from etl.db import get_connection


@contextmanager
def connected_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on successful exit, always close."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def db_rows(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Fetch rows from a schema-initialized test DB."""
    with connected_db(db_path) as conn:
        return conn.execute(sql, params).fetchall()


def db_value(db_path: Path, sql: str, params: tuple[Any, ...] = ()) -> Any:
    """Fetch the first column of the first row from a test DB query."""
    rows = db_rows(db_path, sql, params)
    return rows[0][0] if rows else None


# ── Row-level inserters ─────────────────────────────────────────────────


def insert_computed_daily(
    conn: sqlite3.Connection,
    date: str,
    total: float,
    *,
    us_equity: float = 0.0,
    non_us_equity: float = 0.0,
    crypto: float = 0.0,
    safe_net: float = 0.0,
    liabilities: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO computed_daily "
        "(date, total, us_equity, non_us_equity, crypto, safe_net, liabilities) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (date, total, us_equity, non_us_equity, crypto, safe_net, liabilities),
    )


def insert_ticker(
    conn: sqlite3.Connection,
    date: str,
    ticker: str,
    value: float,
    category: str,
    *,
    subtype: str = "",
    cost_basis: float = 0.0,
) -> None:
    conn.execute(
        "INSERT INTO computed_daily_tickers "
        "(date, ticker, value, category, subtype, cost_basis) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (date, ticker, value, category, subtype, cost_basis),
    )


def insert_close(conn: sqlite3.Connection, symbol: str, date: str, close: float) -> None:
    conn.execute(
        "INSERT INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
        (symbol, date, close),
    )


def insert_fidelity_txn(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    action_type: str = "",
    action: str = "",
    action_kind: str = "",
    symbol: str = "",
    amount: float = 0.0,
    quantity: float = 0.0,
    price: float = 0.0,
    account_number: str = "Z",
    lot_type: str = "",
) -> None:
    conn.execute(
        "INSERT INTO fidelity_transactions "
        "(run_date, account_number, action, action_type, action_kind, symbol, "
        " lot_type, quantity, price, amount) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_date, account_number, action or action_type, action_type, action_kind,
         symbol, lot_type, quantity, price, amount),
    )


def insert_prop_rows(
    db_path: Path,
    rows: list[tuple[str, str, str, float, float]],
) -> None:
    """Insert ``(txn_date, action_kind, ticker, quantity, amount_usd)`` rows
    into the Robinhood-shaped ``prop_transactions`` table used by hypothesis
    property tests."""
    with connected_db(db_path) as conn:
        conn.executemany(
            "INSERT INTO prop_transactions "
            "(txn_date, action_kind, ticker, quantity, amount_usd) VALUES (?,?,?,?,?)",
            rows,
        )


def insert_qianji_txn(
    conn: sqlite3.Connection,
    *,
    date: str,
    kind: str,
    category: str = "",
    amount: float = 0.0,
    is_retirement: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO qianji_transactions "
        "(date, type, category, amount, is_retirement) VALUES (?, ?, ?, ?, ?)",
        (date, kind, category, amount, int(is_retirement)),
    )


# ── Bulk helpers ────────────────────────────────────────────────────────


def ingest_econ_series(db_path: Path, series: dict[str, list[dict[str, Any]]]) -> int:
    """Write FRED time-series to the ``econ_series`` table (full replace).

    Returns the inserted row count.
    """
    with connected_db(db_path) as conn:
        conn.execute("DELETE FROM econ_series")
        count = 0
        for key, points in series.items():
            for pt in points:
                conn.execute(
                    "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                    (key, pt["date"], pt["value"]),
                )
                count += 1
        return count


def ingest_prices(db_path: Path, prices: dict[str, dict[str, float]]) -> None:
    """Bulk-insert daily close prices — ``{"VOO": {"2025-01-02": 500.0, ...}}``."""
    rows: list[tuple[str, str, float]] = [
        (symbol, dt, close)
        for symbol, by_date in prices.items()
        for dt, close in by_date.items()
    ]
    if not rows:
        return
    with connected_db(db_path) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            rows,
        )


# ── Canonical seeds ─────────────────────────────────────────────────────


def seed_clean_db(db_path: Path) -> None:
    """Minimal DB that passes all ``validate_build`` checks.

    Three consecutive trading days, four-category ticker breakdown summing to
    total, prices for every holding > $100, plus a fresh CNY rate. Pre: DB is
    already schema-initialized (use the ``empty_db`` fixture).
    """
    with connected_db(db_path) as conn:
        for dt, total in [("2025-01-02", 100000), ("2025-01-03", 100500), ("2025-01-06", 101000)]:
            insert_computed_daily(
                conn, dt, total,
                us_equity=55000, non_us_equity=15000, crypto=3000, safe_net=27000,
            )
            insert_ticker(conn, dt, "VOO", total * 0.55, "US Equity")
            insert_ticker(conn, dt, "VXUS", total * 0.15, "Non-US Equity")
            insert_ticker(conn, dt, "BTC", total * 0.03, "Crypto")
            insert_ticker(conn, dt, "HYSA", total * 0.27, "Safe Net")
        for sym in ("VOO", "VXUS", "BTC", "HYSA"):
            insert_close(conn, sym, "2025-01-06", 100.0)
        insert_close(conn, "CNY=X", "2025-01-06", 7.25)
