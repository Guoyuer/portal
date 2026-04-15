"""Tests for the robinhood_transactions table schema (Phase 4 — Task 17)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from etl.db import init_db


def test_robinhood_transactions_schema(tmp_path: Path) -> None:
    db = tmp_path / "tm.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(robinhood_transactions)")}
    conn.close()
    assert {"id", "txn_date", "action", "action_kind", "ticker", "quantity", "amount_usd"}.issubset(cols)


def test_robinhood_transactions_unique_constraint_dedups(tmp_path: Path) -> None:
    """Re-inserting the same (txn_date, ticker, action, quantity, amount_usd) row is a no-op under INSERT OR IGNORE."""
    db = tmp_path / "tm.db"
    init_db(db)
    conn = sqlite3.connect(str(db))
    row = ("2024-01-05", "Buy", "buy", "VTI", 5.0, -1150.0, "Vanguard")
    conn.execute(
        "INSERT OR IGNORE INTO robinhood_transactions "
        "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    conn.execute(
        "INSERT OR IGNORE INTO robinhood_transactions "
        "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        row,
    )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM robinhood_transactions").fetchone()[0]
    conn.close()
    assert count == 1
