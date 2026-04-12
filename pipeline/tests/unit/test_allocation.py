"""Tests for compute_daily_allocation — the core portfolio allocation engine.

Seeds timemachine.db with fidelity_transactions + daily_close prices,
and a minimal Qianji-format DB with user_asset + user_bill tables.
Tests the full integration: replay → price lookup → categorization.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

# Ensure yfinance is importable even when not installed.
# The allocation tests don't call yfinance — it's only a transitive import
# via prices.py. We stub the module so prices.py can import without error.
if "yfinance" not in sys.modules:
    _yf = MagicMock(spec=ModuleType, __name__="yfinance", __path__=[])
    _yf.download = MagicMock(return_value=MagicMock(empty=True))
    sys.modules["yfinance"] = _yf

from generate_asset_snapshot.allocation import (  # noqa: E402
    _categorize_ticker,
    _find_price_date,
    _qianji_transaction_dates,
    compute_daily_allocation,
)
from generate_asset_snapshot.db import init_db  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────────


def _init_timemachine(db_path: Path) -> None:
    """Create timemachine.db with schema + seed data."""
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))

    # Fidelity transaction: buy 10 shares of VTI on 01/02/2025
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account, account_number, action, action_type, symbol, lot_type, quantity, amount)"
        " VALUES ('01/02/2025', 'Fidelity taxable', 'Z29133576', 'YOU BOUGHT', 'buy', 'VTI', '', 10, -2500)",
    )
    # Buy 5 shares of VXUS
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account, account_number, action, action_type, symbol, lot_type, quantity, amount)"
        " VALUES ('01/02/2025', 'Fidelity taxable', 'Z29133576', 'YOU BOUGHT', 'buy', 'VXUS', '', 5, -300)",
    )

    # Prices for 3 trading days
    for dt in ("2025-01-02", "2025-01-03", "2025-01-06"):
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VTI', ?, 250.0)", (dt,))
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('VXUS', ?, 60.0)", (dt,))
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', ?, 7.25)", (dt,))

    conn.commit()
    conn.close()


def _init_qianji(db_path: Path) -> None:
    """Create a minimal Qianji-format SQLite DB."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_asset (
            name TEXT PRIMARY KEY,
            money REAL NOT NULL,
            currency TEXT DEFAULT 'USD',
            status INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS user_bill (
            time INTEGER NOT NULL,
            type INTEGER NOT NULL,
            money REAL NOT NULL,
            fromact TEXT DEFAULT '',
            targetact TEXT DEFAULT '',
            extra TEXT DEFAULT '',
            status INTEGER DEFAULT 1
        )
    """)
    # Savings account with $5000
    conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('HYSA', 5000.0, 'USD')")
    # CNY account with 10000 CNY
    conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Alipay', 10000.0, 'CNY')")
    conn.commit()
    conn.close()


def _make_config() -> dict:
    """Minimal config matching the seed data."""
    return {
        "assets": {
            "VTI": {"category": "US Equity", "subtype": "broad"},
            "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
            "HYSA": {"category": "Safe Net", "subtype": ""},
            "CNY Assets": {"category": "Safe Net", "subtype": ""},
        },
        "qianji_accounts": {
            "fidelity_tracked": ["Fidelity taxable", "Roth IRA", "Fidelity Cash Management"],
            "credit": [],
            "cny": ["Alipay"],
            "ticker_map": {"HYSA": "HYSA"},
        },
        "fidelity_accounts": {
            "Z29133576": "FZFXX",
            "238986483": "FDRXX",
            "Z29276228": "SPAXX",
        },
    }


# ── _qianji_transaction_dates ──────────────────────────────────────────────


class TestQianjiTransactionDates:
    def test_returns_sorted_dates(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qianji.db"
        _init_qianji(db_path)
        conn = sqlite3.connect(str(db_path))
        # Jan 3 and Jan 2 (out of order)
        conn.execute("INSERT INTO user_bill (time, type, money, fromact, status) VALUES (1735862400, 0, 10, 'HYSA', 1)")  # 2025-01-03
        conn.execute("INSERT INTO user_bill (time, type, money, fromact, status) VALUES (1735776000, 0, 20, 'HYSA', 1)")  # 2025-01-02
        conn.commit()
        conn.close()
        dates = _qianji_transaction_dates(db_path)
        assert dates == sorted(dates)
        assert len(dates) == 2

    def test_missing_db_returns_empty(self, tmp_path: Path) -> None:
        dates = _qianji_transaction_dates(tmp_path / "nonexistent.db")
        assert dates == []


# ── compute_daily_allocation ──────────────────────────────────────────────


class TestComputeDailyAllocation:
    def test_basic_allocation(self, tmp_path: Path) -> None:
        """Computes values for Fidelity positions × prices."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )

        assert len(results) == 1
        day = results[0]
        assert day["date"] == "2025-01-02"
        # VTI: 10 shares * $250 = $2500
        # VXUS: 5 shares * $60 = $300
        # HYSA: $5000 (from Qianji)
        # Alipay: 10000 CNY / 7.25 ≈ $1379.31 → "CNY Assets"
        total = day["total"]
        assert isinstance(total, float)
        assert total > 0

    def test_skips_weekends(self, tmp_path: Path) -> None:
        """Weekends (Sat/Sun) are skipped."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        # Jan 4-5 2025 is Sat-Sun
        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 6),
        )
        result_dates = [r["date"] for r in results]
        assert "2025-01-04" not in result_dates  # Saturday
        assert "2025-01-05" not in result_dates  # Sunday
        assert "2025-01-02" in result_dates
        assert "2025-01-03" in result_dates
        assert "2025-01-06" in result_dates

    def test_categorization(self, tmp_path: Path) -> None:
        """Tickers are categorized per config assets."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        day = results[0]
        assert day["us_equity"] > 0  # VTI
        assert day["non_us_equity"] > 0  # VXUS
        assert day["safe_net"] > 0  # HYSA + CNY Assets

    def test_ticker_detail(self, tmp_path: Path) -> None:
        """Results include per-ticker detail with category and value."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        tickers = results[0]["tickers"]
        ticker_names = {t["ticker"] for t in tickers}
        assert "VTI" in ticker_names
        assert "VXUS" in ticker_names

        vti = next(t for t in tickers if t["ticker"] == "VTI")
        assert vti["value"] == 2500.0  # 10 * 250
        assert vti["category"] == "US Equity"

    def test_401k_values_included(self, tmp_path: Path) -> None:
        """401k daily values are added to totals."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        config = _make_config()
        config["assets"]["401k sp500"] = {"category": "US Equity", "subtype": "retirement"}

        k401 = {date(2025, 1, 2): {"401k sp500": 50000.0}}

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=config,
            k401_daily=k401,
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        # 401k should add to US Equity
        assert results[0]["us_equity"] >= 50000

    def test_cny_conversion(self, tmp_path: Path) -> None:
        """CNY account balances are converted to USD at historical rate."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        # Alipay: 10000 CNY / 7.25 ≈ 1379.31
        tickers = results[0]["tickers"]
        cny = next((t for t in tickers if t["ticker"] == "CNY Assets"), None)
        assert cny is not None
        assert 1370 < cny["value"] < 1390  # ~10000/7.25

    def test_liabilities_tracked(self, tmp_path: Path) -> None:
        """Negative Qianji balances appear as liabilities."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)

        # Create Qianji DB with a credit card (negative balance)
        conn = sqlite3.connect(str(qj_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_asset (
                name TEXT PRIMARY KEY, money REAL NOT NULL,
                currency TEXT DEFAULT 'USD', status INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_bill (
                time INTEGER, type INTEGER, money REAL,
                fromact TEXT DEFAULT '', targetact TEXT DEFAULT '',
                extra TEXT DEFAULT '', status INTEGER DEFAULT 1
            )
        """)
        conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Credit Card', -2000.0, 'USD')")
        conn.commit()
        conn.close()

        config = _make_config()
        config["qianji_accounts"]["credit"] = ["Credit Card"]

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=config,
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        assert results[0]["liabilities"] < 0

    def test_empty_range(self, tmp_path: Path) -> None:
        """Start after end returns empty results."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 10),
            end=date(2025, 1, 2),
        )
        assert results == []

    def test_no_qianji_db(self, tmp_path: Path) -> None:
        """Missing Qianji DB still produces results (Fidelity-only)."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "nonexistent.db"
        _init_timemachine(db_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        assert len(results) == 1
        # Still has Fidelity positions
        assert results[0]["us_equity"] > 0

    def test_multiple_days_cached_replay(self, tmp_path: Path) -> None:
        """Multiple days reuse cached positions when no new transactions."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)
        _init_qianji(qj_path)

        results = compute_daily_allocation(
            db_path=db_path,
            qj_db=qj_path,
            config=_make_config(),
            k401_daily={},
            start=date(2025, 1, 2),
            end=date(2025, 1, 6),
        )
        # 3 trading days (Thu-Mon, skipping Sat-Sun)
        assert len(results) == 3
        # All should have same positions → similar totals
        totals = [r["total"] for r in results]
        assert all(t > 0 for t in totals)


# ── _find_price_date ───────────────────────────────────────────────────────

import pandas as pd  # noqa: E402


class TestFindPriceDate:
    def _prices(self, dates: list[str]) -> pd.DataFrame:
        return pd.DataFrame(index=pd.to_datetime(dates).date, data={"VOO": [100.0] * len(dates)})

    def test_exact_match_returns_target(self) -> None:
        prices = self._prices(["2025-01-02", "2025-01-03"])
        assert _find_price_date(prices, date(2025, 1, 3), date(2025, 1, 1)) == date(2025, 1, 3)

    def test_walks_back_to_prior_trading_day(self) -> None:
        # Saturday → walk back to Friday
        prices = self._prices(["2025-01-03"])  # Friday
        assert _find_price_date(prices, date(2025, 1, 4), date(2025, 1, 1)) == date(2025, 1, 3)

    def test_stops_at_floor(self) -> None:
        # No prices exist, should stop at floor
        prices = self._prices(["2025-06-01"])
        result = _find_price_date(prices, date(2025, 1, 5), date(2025, 1, 2))
        assert result == date(2025, 1, 2)  # floor, not in prices


# ── _categorize_ticker ──────────────────────────────────────────────────────

class TestCategorizeTicker:
    ASSETS = {
        "VOO": {"category": "US Equity", "subtype": "broad"},
        "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
    }

    def test_positive_value_with_cost_basis(self) -> None:
        row = _categorize_ticker("VOO", 5500.0, self.ASSETS, {"VOO": 5000.0})
        assert row == {
            "ticker": "VOO", "value": 5500.0,
            "category": "US Equity", "subtype": "broad",
            "cost_basis": 5000.0, "gain_loss": 500.0, "gain_loss_pct": 10.0,
        }

    def test_positive_value_without_cost_basis(self) -> None:
        row = _categorize_ticker("VOO", 5500.0, self.ASSETS, {})
        assert row["gain_loss"] == 0
        assert row["gain_loss_pct"] == 0

    def test_negative_value_becomes_liability(self) -> None:
        row = _categorize_ticker("Chase CC", -2000.0, self.ASSETS, {})
        assert row["category"] == "Liability"
        assert row["subtype"] == ""
        assert row["value"] == -2000.0

    def test_missing_asset_raises(self) -> None:
        import pytest as _pytest
        with _pytest.raises(KeyError, match="not in config.assets"):
            _categorize_ticker("UNKNOWN", 100.0, self.ASSETS, {})

    def test_missing_category_raises(self) -> None:
        import pytest as _pytest
        with _pytest.raises(KeyError, match="no 'category'"):
            _categorize_ticker("X", 100.0, {"X": {"subtype": "foo"}}, {})
