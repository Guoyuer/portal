"""Unit tests for the source-agnostic replay primitive (Phase 2 — Task 12)."""
from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from etl.replay import PositionState, replay_transactions  # noqa: F401  (PositionState imported to verify re-export)
from etl.sources import ActionKind


@pytest.fixture
def mini_db(tmp_path: Path) -> Path:
    """Create a tiny SQLite DB with a normalized transactions table."""
    db = tmp_path / "mini.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(
        """
        CREATE TABLE mini_transactions (
            id INTEGER PRIMARY KEY,
            txn_date TEXT NOT NULL,
            action_kind TEXT NOT NULL,
            account TEXT,
            ticker TEXT NOT NULL,
            quantity REAL NOT NULL,
            amount_usd REAL NOT NULL
        );
        """
    )
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-02", ActionKind.BUY.value, "A1", "FOO", 10.0, -1000.0),
            ("2024-01-03", ActionKind.BUY.value, "A1", "FOO", 5.0, -550.0),
            ("2024-02-01", ActionKind.SELL.value, "A1", "FOO", -3.0, 330.0),
            ("2024-03-01", ActionKind.DIVIDEND.value, "A1", "FOO", 0.0, 12.0),
        ],
    )
    conn.commit()
    conn.close()
    return db


def test_replay_accumulates_position_and_cost_basis(mini_db: Path) -> None:
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 2, 15))
    assert set(states.keys()) == {"FOO"}
    foo = states["FOO"]
    assert foo.quantity == pytest.approx(12.0)  # 10 + 5 - 3
    # Cost basis reduced proportionally on sell: 1550 * (1 - 3/15) = 1240
    assert foo.cost_basis_usd == pytest.approx(1240.0, rel=1e-3)


def test_replay_respects_as_of_cutoff(mini_db: Path) -> None:
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 1, 2))
    foo = states["FOO"]
    assert foo.quantity == pytest.approx(10.0)
    assert foo.cost_basis_usd == pytest.approx(1000.0)


def test_replay_dropped_zero_positions(mini_db: Path) -> None:
    """Fully sold-out tickers shouldn't appear in the result."""
    conn = sqlite3.connect(str(mini_db))
    conn.executemany(
        "INSERT INTO mini_transactions (txn_date, action_kind, account, ticker, quantity, amount_usd) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("2024-01-10", ActionKind.BUY.value, "A1", "BAR", 5.0, -200.0),
            ("2024-01-20", ActionKind.SELL.value, "A1", "BAR", -5.0, 220.0),
        ],
    )
    conn.commit()
    conn.close()
    states = replay_transactions(mini_db, "mini_transactions", date(2024, 2, 15))
    assert "BAR" not in states
