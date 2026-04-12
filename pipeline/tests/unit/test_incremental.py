"""Tests for incremental build and cross-check verification."""
from __future__ import annotations

from datetime import date

import pytest

from etl.db import get_connection, init_db
from etl.incremental import (
    append_daily,
    get_last_computed_date,
)


@pytest.fixture()
def db(tmp_path):
    p = tmp_path / "test.db"
    init_db(p)
    return p


def _insert_daily(db_path, rows):
    conn = get_connection(db_path)
    for r in rows:
        conn.execute(
            "INSERT INTO computed_daily (date, total, us_equity, non_us_equity, crypto, safe_net)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (r["date"], r["total"], r["us_equity"], r["non_us_equity"], r["crypto"], r["safe_net"]),
        )
    conn.commit()
    conn.close()


_DAY1 = {"date": "2025-01-02", "total": 100, "us_equity": 50, "non_us_equity": 20, "crypto": 10, "safe_net": 20}
_DAY2 = {"date": "2025-01-03", "total": 110, "us_equity": 55, "non_us_equity": 22, "crypto": 11, "safe_net": 22}


# ── get_last_computed_date ──────────────────────────────────────────────────


class TestGetLastComputedDate:
    def test_empty_db(self, db):
        assert get_last_computed_date(db) is None

    def test_returns_latest(self, db):
        _insert_daily(db, [_DAY1, _DAY2])
        assert get_last_computed_date(db) == date(2025, 1, 3)


# ── append_daily ────────────────────────────────────────────────────────────


class TestAppendDaily:
    def test_appends_new_rows(self, db):
        _insert_daily(db, [_DAY1])
        new = [{"date": "2025-01-03", "total": 110, "us_equity": 55,
                "non_us_equity": 22, "crypto": 11, "safe_net": 22,
                "liabilities": 0, "tickers": []}]
        assert append_daily(db, new) == 1
        conn = get_connection(db)
        assert conn.execute("SELECT COUNT(*) FROM computed_daily").fetchone()[0] == 2
        conn.close()

    def test_skips_existing_dates(self, db):
        _insert_daily(db, [_DAY1])
        new = [
            {"date": "2025-01-02", "total": 999, "us_equity": 0,
             "non_us_equity": 0, "crypto": 0, "safe_net": 0,
             "liabilities": 0, "tickers": []},
            {"date": "2025-01-03", "total": 110, "us_equity": 55,
             "non_us_equity": 22, "crypto": 11, "safe_net": 22,
             "liabilities": 0, "tickers": []},
        ]
        assert append_daily(db, new) == 1
        conn = get_connection(db)
        row = conn.execute("SELECT total FROM computed_daily WHERE date = '2025-01-02'").fetchone()
        conn.close()
        assert row[0] == 100  # original preserved

    def test_appends_tickers(self, db):
        new = [{"date": "2025-01-02", "total": 100, "us_equity": 50,
                "non_us_equity": 20, "crypto": 10, "safe_net": 20,
                "liabilities": 0,
                "tickers": [{"ticker": "VOO", "value": 50, "category": "US Equity",
                             "subtype": "S&P 500", "cost_basis": 40,
                             "gain_loss": 10, "gain_loss_pct": 25}]}]
        append_daily(db, new)
        conn = get_connection(db)
        row = conn.execute("SELECT ticker, value FROM computed_daily_tickers WHERE date = '2025-01-02'").fetchone()
        conn.close()
        assert row == ("VOO", 50)

    def test_empty_input(self, db):
        assert append_daily(db, []) == 0
