"""Tests for Qianji balance replay."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from etl.qianji.balances import qianji_balances_at, qianji_currencies

# ── Helpers ──────────────────────────────────────────────────────────────────

def _create_qianji_db(db_path: Path, assets: list[tuple], bills: list[dict]) -> None:
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute("CREATE TABLE user_asset (name TEXT, money REAL, currency TEXT, status INTEGER)")
        conn.execute(
            "CREATE TABLE user_bill ("
            "id INTEGER PRIMARY KEY, type INTEGER, money REAL, fromact TEXT, targetact TEXT,"
            "remark TEXT, time REAL, cateid INTEGER, extra TEXT, status INTEGER)"
        )
        conn.executemany(
            "INSERT INTO user_asset (name, money, currency, status) VALUES (?, ?, ?, ?)",
            [(name, money, currency, rest[0] if rest else 0) for name, money, currency, *rest in assets],
        )
        conn.executemany(
            "INSERT INTO user_bill (id, type, money, fromact, targetact, remark, time, cateid, extra, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
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
                )
                for i, bill in enumerate(bills)
            ],
        )
        conn.commit()


def _ts(year: int, month: int, day: int) -> float:
    return datetime(year, month, day, 12, 0, 0, tzinfo=UTC).timestamp()


def _snapshot(
    tmp_path: Path,
    assets: list[tuple],
    bills: list[dict],
    as_of: date | None = date(2025, 3, 1),
) -> dict[str, float]:
    db = tmp_path / "qianji.db"
    _create_qianji_db(db, assets, bills)
    return qianji_balances_at(db, as_of=as_of)


# ── qianji_balances_at: balance replay ──────────────────────────────────────

class TestQianjiBalances:
    def test_missing_db(self, tmp_path: Path) -> None:
        assert qianji_balances_at(tmp_path / "nonexistent.db") == {}

    def test_current_balances_no_date(self, tmp_path: Path) -> None:
        snapshot = _snapshot(
            tmp_path,
            [("Checking", 5000.0, "USD"), ("Savings", 10000.0, "USD")],
            [],
            as_of=None,
        )
        assert snapshot["Checking"] == pytest.approx(5000.0)
        assert snapshot["Savings"] == pytest.approx(10000.0)

    @pytest.mark.parametrize(
        ("assets", "bills", "expected"),
        [
            ([("Checking", 4000.0, "USD")],
             [{"type": 0, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)}],
             {"Checking": 5000.0}),
            ([("Checking", 6000.0, "USD")],
             [{"type": 1, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)}],
             {"Checking": 5000.0}),
            ([("Checking", 4000.0, "USD"), ("Savings", 6000.0, "USD")],
             [{"type": 2, "money": 1000.0, "fromact": "Checking", "targetact": "Savings",
               "time": _ts(2025, 3, 15)}],
             {"Checking": 5000.0, "Savings": 5000.0}),
            ([("Credit Card", -500.0, "USD"), ("Checking", 9500.0, "USD")],
             [{"type": 3, "money": 500.0, "fromact": "Checking", "targetact": "Credit Card",
               "time": _ts(2025, 3, 15)}],
             {"Checking": 10000.0, "Credit Card": -1000.0}),
            ([("USD Account", 9000.0, "USD"), ("CNY Account", 7000.0, "CNY")],
             [{"type": 2, "money": 1000.0, "fromact": "USD Account", "targetact": "CNY Account",
               "time": _ts(2025, 3, 15), "extra": '{"curr": {"ss": "USD", "ts": "CNY", "tv": 7000.0}}'}],
             {"USD Account": 10000.0, "CNY Account": 0.0}),
            ([("Checking", 5000.0, "USD")],
             [{"type": 0, "money": 999.0, "fromact": "Checking", "time": _ts(2025, 3, 15), "status": 0}],
             {"Checking": 5000.0}),
            ([("Checking", 4000.0, "USD")],
             [{"type": 0, "money": 1000.0, "fromact": "Checking", "time": _ts(2025, 3, 1)}],
             {"Checking": 4000.0}),
            ([("Checking", 3500.0, "USD")],
             [
                 {"type": 0, "money": 500.0, "fromact": "Checking", "time": _ts(2025, 3, 10)},
                 {"type": 1, "money": 2000.0, "fromact": "Checking", "time": _ts(2025, 3, 15)},
             ],
             {"Checking": 2000.0}),
        ],
    )
    def test_replays_balances_to_as_of(self, tmp_path: Path, assets: list[tuple], bills: list[dict], expected: dict[str, float]) -> None:
        snapshot = _snapshot(tmp_path, assets, bills)
        for account, amount in expected.items():
            assert snapshot[account] == pytest.approx(amount)

    def test_unknown_bill_type_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        db = tmp_path / "qianji.db"
        _create_qianji_db(
            db,
            [("Checking", 5000.0, "USD")],
            [{"type": 4, "money": 999.0, "fromact": "Checking", "time": _ts(2025, 3, 15)}],
        )
        with caplog.at_level(logging.WARNING, logger="etl.qianji"):
            snapshot = qianji_balances_at(db, as_of=date(2025, 3, 1))
        assert snapshot["Checking"] == pytest.approx(5000.0)
        assert any("bill_type=4" in rec.message for rec in caplog.records)

    def test_inactive_asset_excluded(self, tmp_path: Path) -> None:
        snapshot = _snapshot(tmp_path, [("Active", 100.0, "USD"), ("Closed", 200.0, "USD", 1)], [], as_of=None)
        assert "Active" in snapshot
        assert "Closed" not in snapshot


# ── qianji_balances_at: currencies ──────────────────────────────────────────

class TestQianjiCurrencies:
    def test_returns_currencies(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        _create_qianji_db(db, [("Checking", 5000.0, "USD"), ("Alipay", 30000.0, "CNY")], [])
        currencies = qianji_currencies(db)
        assert currencies["Checking"] == "USD"
        assert currencies["Alipay"] == "CNY"

    def test_null_currency_defaults_usd(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        _create_qianji_db(db, [("Wallet", 100.0, None)], [])
        assert qianji_currencies(db)["Wallet"] == "USD"

    def test_missing_db_empty_currencies(self, tmp_path: Path) -> None:
        assert qianji_currencies(tmp_path / "nonexistent.db") == {}

    def test_excludes_inactive(self, tmp_path: Path) -> None:
        db = tmp_path / "qianji.db"
        _create_qianji_db(db, [("Active", 100.0, "USD"), ("Closed", 200.0, "EUR", 1)], [])
        currencies = qianji_currencies(db)
        assert "Active" in currencies
        assert "Closed" not in currencies
