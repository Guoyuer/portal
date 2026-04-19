"""Tests for the robinhood_transactions table schema (Phase 4 — Task 17)."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_robinhood_transactions_schema(empty_db: Path) -> None:
    conn = sqlite3.connect(str(empty_db))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(robinhood_transactions)")}
    conn.close()
    assert {"id", "txn_date", "action", "action_kind", "ticker", "quantity", "amount_usd"}.issubset(cols)


def test_robinhood_transactions_allows_duplicate_rows(empty_db: Path) -> None:
    """Same-day duplicate trades (identical txn_date/ticker/action/qty/amount)
    are preserved as two rows — Robinhood CSVs legitimately emit such pairs
    for recurring orders of identical size. Idempotent re-ingest is handled
    by the range-replace pattern in :func:`etl.sources.robinhood.ingest`, not by
    a UNIQUE constraint at the schema level.
    """
    conn = sqlite3.connect(str(empty_db))
    row = ("2024-01-05", "Buy", "buy", "VTI", 5.0, -1150.0, "Vanguard")
    for _ in range(2):
        conn.execute(
            "INSERT INTO robinhood_transactions "
            "(txn_date, action, action_kind, ticker, quantity, amount_usd, raw_description) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            row,
        )
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM robinhood_transactions").fetchone()[0]
    conn.close()
    assert count == 2
