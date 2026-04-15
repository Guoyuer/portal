"""Tests for the timemachine module — Qianji replay only.

The Fidelity replay engine moved to :mod:`etl.replay`; its tests live in
``test_replay_primitive.py``. The CSV-based ``ingest`` / ``verify`` helpers
were deleted along with the legacy CSV replay path.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etl.timemachine import (
    replay_qianji,
    replay_qianji_currencies,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_qianji_db(db_path: Path, assets: list[tuple[str, float, str]], bills: list[dict]) -> None:
    """Create a minimal Qianji SQLite DB with user_asset and user_bill tables."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE user_asset ("
        "  name TEXT, money REAL, currency TEXT, status INTEGER"
        ")"
    )
    conn.execute(
        "CREATE TABLE user_bill ("
        "  id INTEGER PRIMARY KEY, type INTEGER, money REAL,"
        "  fromact TEXT, targetact TEXT, remark TEXT, time REAL,"
        "  cateid INTEGER, extra TEXT, status INTEGER"
        ")"
    )
    for name, money, currency in assets:
        conn.execute(
            "INSERT INTO user_asset (name, money, currency, status) VALUES (?, ?, ?, 0)",
            (name, money, currency),
        )
    for i, bill in enumerate(bills):
        conn.execute(
            "INSERT INTO user_bill (id, type, money, fromact, targetact, remark, time, cateid, extra, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                i + 1,
                bill["type"],
                bill["money"],
                bill.get("fromact", ""),
                bill.get("targetact", ""),
                bill.get("remark", ""),
                bill["time"],
                bill.get("cateid", 0),
                bill.get("extra"),
                bill.get("status", 1),
            ),
        )
    conn.commit()
    conn.close()


def _ts(year: int, month: int, day: int) -> float:
    """Create a UTC timestamp for the given date at noon."""
    return datetime(year, month, day, 12, 0, 0, tzinfo=UTC).timestamp()


# ── replay_qianji ───────────────────────────────────────────────────────────

class TestReplayQianji:
    def test_missing_db(self, tmp_path: Path) -> None:
        result = replay_qianji(tmp_path / "nonexistent.db")
        assert result == {}

    def test_current_balances_no_date(self, tmp_path: Path) -> None:
        """Without as_of, return current balances."""
        db = tmp_path / "qianji.db"
        _create_qianji_db(db, [("Checking", 5000.0, "USD"), ("Savings", 10000.0, "USD")], [])
        result = replay_qianji(db)
        assert result["Checking"] == pytest.approx(5000.0)
        assert result["Savings"] == pytest.approx(10000.0)

    def test_reverse_expense(self, tmp_path: Path) -> None:
        """Expense after as_of should be added back (reversed)."""
        db = tmp_path / "qianji.db"
        # Current balance: $4000 (after spending $1000)
        _create_qianji_db(
            db,
            [("Checking", 4000.0, "USD")],
            [{"type": 0, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Before the expense, balance was 4000 + 1000 = 5000
        assert result["Checking"] == pytest.approx(5000.0)

    def test_reverse_income(self, tmp_path: Path) -> None:
        """Income after as_of should be subtracted (reversed)."""
        db = tmp_path / "qianji.db"
        # Current balance: $6000 (after receiving $1000 income)
        _create_qianji_db(
            db,
            [("Checking", 6000.0, "USD")],
            [{"type": 1, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Before the income, balance was 6000 - 1000 = 5000
        assert result["Checking"] == pytest.approx(5000.0)

    def test_reverse_transfer(self, tmp_path: Path) -> None:
        """Transfer after as_of should be reversed on both sides."""
        db = tmp_path / "qianji.db"
        # Current: Checking=4000, Savings=6000 (after transferring 1000 from Checking to Savings)
        _create_qianji_db(
            db,
            [("Checking", 4000.0, "USD"), ("Savings", 6000.0, "USD")],
            [{"type": 2, "money": 1000.0, "fromact": "Checking", "targetact": "Savings",
              "time": _ts(2025, 3, 15)}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Before transfer: Checking was 4000+1000=5000, Savings was 6000-1000=5000
        assert result["Checking"] == pytest.approx(5000.0)
        assert result["Savings"] == pytest.approx(5000.0)

    def test_reverse_repayment(self, tmp_path: Path) -> None:
        """Repayment (type 3) behaves like transfer."""
        db = tmp_path / "qianji.db"
        _create_qianji_db(
            db,
            [("Credit Card", -500.0, "USD"), ("Checking", 9500.0, "USD")],
            [{"type": 3, "money": 500.0, "fromact": "Checking", "targetact": "Credit Card",
              "time": _ts(2025, 3, 15)}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Before repayment: Checking was 9500+500=10000, Credit Card was -500-(-500)=-1000
        assert result["Checking"] == pytest.approx(10000.0)
        assert result["Credit Card"] == pytest.approx(-1000.0)

    def test_cross_currency_transfer(self, tmp_path: Path) -> None:
        """Cross-currency transfer uses tv for the target account."""
        db = tmp_path / "qianji.db"
        extra = '{"curr": {"ss": "USD", "ts": "CNY", "tv": 7000.0}}'
        _create_qianji_db(
            db,
            [("USD Account", 9000.0, "USD"), ("CNY Account", 7000.0, "CNY")],
            [{"type": 2, "money": 1000.0, "fromact": "USD Account", "targetact": "CNY Account",
              "time": _ts(2025, 3, 15), "extra": extra}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Before: USD was 9000+1000=10000, CNY was 7000-7000=0
        assert result["USD Account"] == pytest.approx(10000.0)
        assert result["CNY Account"] == pytest.approx(0.0)

    def test_inactive_bills_ignored(self, tmp_path: Path) -> None:
        """Bills with status != 1 should not be replayed."""
        db = tmp_path / "qianji.db"
        _create_qianji_db(
            db,
            [("Checking", 5000.0, "USD")],
            [{"type": 0, "money": 999.0, "fromact": "Checking", "time": _ts(2025, 3, 15), "status": 0}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Inactive bill not reversed, balance stays at current
        assert result["Checking"] == pytest.approx(5000.0)

    def test_bills_on_cutoff_day_not_reversed(self, tmp_path: Path) -> None:
        """Bills on as_of date (before 23:59:59) should NOT be reversed."""
        db = tmp_path / "qianji.db"
        # Bill at noon on March 1 — cutoff is end of March 1
        _create_qianji_db(
            db,
            [("Checking", 4000.0, "USD")],
            [{"type": 0, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 1)}],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Bill at noon on March 1 is before cutoff (23:59:59 March 1), so NOT reversed
        assert result["Checking"] == pytest.approx(4000.0)

    def test_multiple_transactions(self, tmp_path: Path) -> None:
        """Multiple mixed transactions should all be reversed correctly."""
        db = tmp_path / "qianji.db"
        _create_qianji_db(
            db,
            [("Checking", 3500.0, "USD")],
            [
                # Expense of 500 on March 10
                {"type": 0, "money": 500.0, "fromact": "Checking", "time": _ts(2025, 3, 10)},
                # Income of 2000 on March 15
                {"type": 1, "money": 2000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)},
            ],
        )
        result = replay_qianji(db, as_of=date(2025, 3, 1))
        # Reverse: +500 (expense), -2000 (income) → 3500 + 500 - 2000 = 2000
        assert result["Checking"] == pytest.approx(2000.0)

    def test_inactive_asset_excluded(self, tmp_path: Path) -> None:
        """Assets with status != 0 should not appear."""
        db = tmp_path / "qianji.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE user_asset (name TEXT, money REAL, currency TEXT, status INTEGER)"
        )
        conn.execute(
            "CREATE TABLE user_bill (id INTEGER PRIMARY KEY, type INTEGER, money REAL,"
            " fromact TEXT, targetact TEXT, remark TEXT, time REAL, cateid INTEGER, extra TEXT, status INTEGER)"
        )
        conn.execute("INSERT INTO user_asset VALUES ('Active', 100.0, 'USD', 0)")
        conn.execute("INSERT INTO user_asset VALUES ('Closed', 200.0, 'USD', 1)")
        conn.commit()
        conn.close()
        result = replay_qianji(db)
        assert "Active" in result
        assert "Closed" not in result


# ── replay_qianji_currencies ────────────────────────────────────────────────

class TestReplayQianjiCurrencies:
    def test_returns_currencies(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        _create_qianji_db(
            db,
            [("Checking", 5000.0, "USD"), ("Alipay", 30000.0, "CNY")],
            [],
        )
        currencies = replay_qianji_currencies(db)
        assert currencies["Checking"] == "USD"
        assert currencies["Alipay"] == "CNY"

    def test_null_currency_defaults_usd(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE user_asset (name TEXT, money REAL, currency TEXT, status INTEGER)"
        )
        conn.execute("INSERT INTO user_asset VALUES ('Wallet', 100.0, NULL, 0)")
        conn.commit()
        conn.close()
        currencies = replay_qianji_currencies(db)
        assert currencies["Wallet"] == "USD"

    def test_missing_db(self, tmp_path: Path) -> None:
        result = replay_qianji_currencies(tmp_path / "nonexistent.db")
        assert result == {}

    def test_excludes_inactive(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        conn = sqlite3.connect(str(db))
        conn.execute(
            "CREATE TABLE user_asset (name TEXT, money REAL, currency TEXT, status INTEGER)"
        )
        conn.execute("INSERT INTO user_asset VALUES ('Active', 100.0, 'USD', 0)")
        conn.execute("INSERT INTO user_asset VALUES ('Closed', 200.0, 'EUR', 1)")
        conn.commit()
        conn.close()
        currencies = replay_qianji_currencies(db)
        assert "Active" in currencies
        assert "Closed" not in currencies


