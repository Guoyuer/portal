"""Test-only helpers for seeding the timemachine SQLite DB.

These functions are used exclusively by tests under ``pipeline/tests/`` to
populate minimal DB state before exercising production code. Previously
they lived in ``etl/db.py``; moving them here makes the boundary between
production code (no callers = dead) and test infrastructure explicit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from etl.db import get_connection


def ingest_econ_series(db_path: Path, series: dict[str, list[dict[str, Any]]]) -> int:
    """Write FRED time-series to the ``econ_series`` table. Returns row count.

    Replaces the whole table (DELETE then INSERT) so tests can re-seed
    cleanly between cases.
    """
    conn = get_connection(db_path)
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


def ingest_prices(db_path: Path, prices: dict[str, dict[str, float]]) -> None:
    """Bulk-insert daily close prices into the ``daily_close`` table.

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
