"""Tests for compute_daily_allocation — the core portfolio allocation engine.

Seeds timemachine.db with fidelity_transactions + daily_close prices,
and a minimal Qianji-format DB with user_asset + user_bill tables.
Tests the full integration: replay → price lookup → categorization.
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# Ensure yfinance is importable even when not installed.
# The allocation tests don't call yfinance — it's only a transitive import
# via prices.py. We stub the module so prices.py can import without error.
if "yfinance" not in sys.modules:
    _yf = MagicMock(spec=ModuleType, __name__="yfinance", __path__=[])
    _yf.download = MagicMock(return_value=MagicMock(empty=True))
    sys.modules["yfinance"] = _yf

from etl.allocation import (  # noqa: E402
    _add_qianji_balances,
    _build_allocation_row,
    _categorize_ticker,
    _find_price_date,
    _qianji_transaction_dates,
    _resolve_date_windows,
    compute_daily_allocation,
)
from etl.db import init_db  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────────


def _init_timemachine(db_path: Path) -> None:
    """Create timemachine.db with schema + seed data."""
    init_db(db_path)
    conn = sqlite3.connect(str(db_path))

    # Fidelity transaction: buy 10 shares of VTI on 2025-01-02
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account_number, action, action_type, action_kind,"
        "  symbol, lot_type, quantity, amount)"
        " VALUES ('2025-01-02', 'Z29133576', 'YOU BOUGHT', 'buy', 'buy', 'VTI', '', 10, -2500)",
    )
    # Buy 5 shares of VXUS
    conn.execute(
        "INSERT INTO fidelity_transactions"
        " (run_date, account_number, action, action_type, action_kind,"
        "  symbol, lot_type, quantity, amount)"
        " VALUES ('2025-01-02', 'Z29133576', 'YOU BOUGHT', 'buy', 'buy', 'VXUS', '', 5, -300)",
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
    _create_qianji_schema(conn)
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
            "CNY Cash": {"category": "Safe Net", "subtype": ""},
        },
        "qianji_accounts": {
            "fidelity_tracked": ["Fidelity taxable", "Roth IRA", "Fidelity Cash Management"],
            "credit": [],
            "cny": ["Alipay"],
            "ticker_map": {"HYSA": "HYSA", "Alipay": "CNY Cash"},
        },
        "fidelity_accounts": {
            "Z29133576": "FZFXX",
            "238986483": "FDRXX",
            "Z29276228": "SPAXX",
        },
    }


def _create_qianji_schema(conn: sqlite3.Connection) -> None:
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


@pytest.fixture()
def allocation_paths(tmp_path: Path) -> tuple[Path, Path]:
    db_path = tmp_path / "timemachine.db"
    qj_path = tmp_path / "qianji.db"
    _init_timemachine(db_path)
    _init_qianji(qj_path)
    return db_path, qj_path


def _compute_allocation(
    paths: tuple[Path, Path],
    *,
    config: dict | None = None,
    start: date = date(2025, 1, 2),
    end: date = date(2025, 1, 2),
) -> list[dict]:
    db_path, qj_path = paths
    return compute_daily_allocation(
        db_path=db_path,
        qj_db=qj_path,
        config=config or _make_config(),
        start=start,
        end=end,
    )


# ── _qianji_transaction_dates ──────────────────────────────────────────────


class TestQianjiTransactionDates:
    def test_returns_sorted_dates(self, tmp_path: Path) -> None:
        db_path = tmp_path / "qianji.db"
        _init_qianji(db_path)
        conn = sqlite3.connect(str(db_path))
        # Jan 3 and Jan 2 (out of order)
        conn.executemany(
            "INSERT INTO user_bill (time, type, money, fromact, status) VALUES (?, 0, ?, 'HYSA', 1)",
            [
                (1735862400, 10),  # 2025-01-03
                (1735776000, 20),  # 2025-01-02
            ],
        )
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
    def test_basic_allocation(self, allocation_paths: tuple[Path, Path]) -> None:
        """Computes values for Fidelity positions × prices."""
        results = _compute_allocation(allocation_paths)

        assert len(results) == 1
        day = results[0]
        assert day["date"] == "2025-01-02"
        # VTI: 10 shares * $250 = $2500
        # VXUS: 5 shares * $60 = $300
        # HYSA: $5000 (from Qianji)
        # Alipay: 10000 CNY / 7.25 ≈ $1379.31 → "CNY Cash"
        total = day["total"]
        assert isinstance(total, float)
        assert total > 0

    def test_skips_weekends(self, allocation_paths: tuple[Path, Path]) -> None:
        """Weekends (Sat/Sun) are skipped."""
        results = _compute_allocation(allocation_paths, end=date(2025, 1, 6))
        result_dates = [r["date"] for r in results]
        assert "2025-01-04" not in result_dates  # Saturday
        assert "2025-01-05" not in result_dates  # Sunday
        assert "2025-01-02" in result_dates
        assert "2025-01-03" in result_dates
        assert "2025-01-06" in result_dates

    def test_categorization(self, allocation_paths: tuple[Path, Path]) -> None:
        """Tickers are categorized per config assets."""
        results = _compute_allocation(allocation_paths)
        day = results[0]
        assert day["us_equity"] > 0  # VTI
        assert day["non_us_equity"] > 0  # VXUS
        assert day["safe_net"] > 0  # HYSA + CNY Cash

    def test_ticker_detail(self, allocation_paths: tuple[Path, Path]) -> None:
        """Results include per-ticker detail with category and value."""
        results = _compute_allocation(allocation_paths)
        tickers = results[0]["tickers"]
        ticker_names = {t["ticker"] for t in tickers}
        assert "VTI" in ticker_names
        assert "VXUS" in ticker_names

        vti = next(t for t in tickers if t["ticker"] == "VTI")
        assert vti["value"] == 2500.0  # 10 * 250
        assert vti["category"] == "US Equity"

    def test_unknown_fidelity_account_falls_back_to_fzfxx(self, tmp_path: Path) -> None:
        """Cash in a Fidelity account not listed in config.fidelity_accounts routes to FZFXX."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"

        # Empty qianji DB (schema only, no accounts) so only Fidelity cash drives the result
        with closing(sqlite3.connect(str(qj_path))) as qj_conn:
            _create_qianji_schema(qj_conn)

        # Seed a deposit in an UNKNOWN account number (not in fidelity_accounts)
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO fidelity_transactions"
            " (run_date, account_number, action, action_type, action_kind,"
            "  symbol, lot_type, quantity, amount)"
            " VALUES ('2025-01-02', 'UNKNOWN999', 'DEPOSIT', 'deposit', 'deposit', '', '', 0, 1000)",
        )
        conn.execute("INSERT INTO daily_close (symbol, date, close) VALUES ('CNY=X', '2025-01-02', 7.25)")
        conn.commit()
        conn.close()

        config = {
            "assets": {"FZFXX": {"category": "Safe Net", "subtype": ""}},
            "qianji_accounts": {
                "fidelity_tracked": ["Fidelity taxable"],
                "credit": [], "cny": [], "ticker_map": {},
            },
            "fidelity_accounts": {},  # empty → force fallback for all accounts
        }

        results = _compute_allocation((db_path, qj_path), config=config)

        assert len(results) == 1
        tickers = {t["ticker"]: t["value"] for t in results[0]["tickers"]}
        assert "FZFXX" in tickers
        assert tickers["FZFXX"] == 1000.0

    def test_401k_values_included(self, allocation_paths: tuple[Path, Path]) -> None:
        """Empower 401k values (from empower_snapshots/empower_funds) are added to totals."""
        db_path, _ = allocation_paths

        # Seed an Empower snapshot + fund row so :mod:`etl.sources.empower` picks it up.
        conn = sqlite3.connect(str(db_path))
        conn.execute("INSERT INTO empower_snapshots (snapshot_date) VALUES ('2025-01-02')")
        sid = conn.execute(
            "SELECT id FROM empower_snapshots WHERE snapshot_date = '2025-01-02'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval)"
            " VALUES (?, '856917729', '401k sp500', 100.0, 500.0, 50000.0)",
            (sid,),
        )
        conn.commit()
        conn.close()

        config = _make_config()
        config["assets"]["401k sp500"] = {"category": "US Equity", "subtype": "retirement"}

        results = _compute_allocation(allocation_paths, config=config)
        # 401k should add to US Equity
        assert results[0]["us_equity"] >= 50000

    def test_cny_conversion(self, allocation_paths: tuple[Path, Path]) -> None:
        """CNY account balances are converted to USD at historical rate."""
        results = _compute_allocation(allocation_paths)
        # Alipay: 10000 CNY / 7.25 ≈ 1379.31
        tickers = results[0]["tickers"]
        cny = next((t for t in tickers if t["ticker"] == "CNY Cash"), None)
        assert cny is not None
        assert 1370 < cny["value"] < 1390  # ~10000/7.25

    def test_liabilities_tracked(self, tmp_path: Path) -> None:
        """Negative Qianji balances appear as liabilities."""
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)

        # Create Qianji DB with a credit card (negative balance)
        with closing(sqlite3.connect(str(qj_path))) as conn:
            _create_qianji_schema(conn)
            conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Credit Card', -2000.0, 'USD')")

        config = _make_config()
        config["qianji_accounts"]["credit"] = ["Credit Card"]

        results = _compute_allocation((db_path, qj_path), config=config)
        assert results[0]["liabilities"] < 0

    def test_empty_range(self, allocation_paths: tuple[Path, Path]) -> None:
        """Start after end returns empty results."""
        results = _compute_allocation(allocation_paths, start=date(2025, 1, 10))
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
            start=date(2025, 1, 2),
            end=date(2025, 1, 2),
        )
        assert len(results) == 1
        # Still has Fidelity positions
        assert results[0]["us_equity"] > 0

    def test_multiple_days_cached_replay(self, allocation_paths: tuple[Path, Path]) -> None:
        """Multiple days reuse cached positions when no new transactions."""
        results = _compute_allocation(allocation_paths, end=date(2025, 1, 6))
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

    @pytest.mark.parametrize(
        ("price_dates", "target", "expected"),
        [
            pytest.param(["2025-01-02", "2025-01-03"], date(2025, 1, 3), date(2025, 1, 3), id="exact"),
            pytest.param(["2025-01-03"], date(2025, 1, 4), date(2025, 1, 3), id="saturday-to-friday"),
            pytest.param(["2026-04-10"], date(2026, 4, 12), date(2026, 4, 10), id="weekend-t-minus-one"),
            pytest.param(["2025-06-01"], date(2025, 1, 5), date(2025, 1, 5), id="target-before-data"),
        ],
    )
    def test_find_price_date(self, price_dates: list[str], target: date, expected: date) -> None:
        assert _find_price_date(self._prices(price_dates), target) == expected


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


# ── _add_qianji_balances ───────────────────────────────────────────────────


class TestAddQianjiBalances:
    def test_cny_converts_to_usd(self) -> None:
        ticker_values: dict[str, float] = {}
        _add_qianji_balances(
            ticker_values,
            qj_balances={"Alipay": 7250.0},
            currencies={"Alipay": "CNY"},
            ticker_map={"Alipay": "Alipay Funds"},
            assets={"Alipay Funds": {"category": "Non-US Equity"}},
            cny_rate=7.25,
            skip_accounts=frozenset(),
        )
        assert ticker_values == {"Alipay Funds": 1000.0}

    def test_mapped_cny_goes_to_cny_cash(self) -> None:
        """CNY accounts with an explicit ticker_map entry route to the mapped ticker."""
        ticker_values: dict[str, float] = {}
        _add_qianji_balances(
            ticker_values,
            qj_balances={"建行卡": 7250.0},
            currencies={"建行卡": "CNY"},
            ticker_map={"建行卡": "CNY Cash"},
            assets={"CNY Cash": {"category": "Safe Net"}},
            cny_rate=7.25,
            skip_accounts=frozenset(),
        )
        assert ticker_values == {"CNY Cash": 1000.0}

    def test_unmapped_cny_warns_and_excluded(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Unmapped CNY accounts warn + are excluded, symmetric with USD."""
        ticker_values: dict[str, float] = {}
        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            _add_qianji_balances(
                ticker_values,
                qj_balances={"RandomCNY": 7250.0},
                currencies={"RandomCNY": "CNY"},
                ticker_map={},
                assets={},
                cny_rate=7.25,
                skip_accounts=frozenset(),
            )
        assert ticker_values == {}
        warnings = [r for r in caplog.records if "RandomCNY" in r.message]
        assert len(warnings) == 1
        assert "ticker_map" in warnings[0].message
        assert "CNY" in warnings[0].message  # currency preserved
        assert "7250" in warnings[0].message  # original balance preserved

    def test_negative_balance_treated_as_liability(self) -> None:
        ticker_values: dict[str, float] = {}
        _add_qianji_balances(
            ticker_values,
            qj_balances={"Visa Card": -500.0},
            currencies={"Visa Card": "USD"},
            ticker_map={},
            assets={},
            cny_rate=7.25,
            skip_accounts=frozenset(),
        )
        assert ticker_values == {"Visa Card": -500.0}  # Account name becomes ticker

    def test_skipped_accounts_ignored(self) -> None:
        ticker_values: dict[str, float] = {}
        _add_qianji_balances(
            ticker_values,
            qj_balances={"Fidelity taxable": 10000.0},
            currencies={},
            ticker_map={},
            assets={},
            cny_rate=7.25,
            skip_accounts=frozenset({"Fidelity taxable"}),
        )
        assert ticker_values == {}


# ── _resolve_date_windows ──────────────────────────────────────────────────


class TestResolveDateWindows:
    def test_walks_back_to_nearest_price_date(self) -> None:
        prices = pd.DataFrame(
            index=pd.to_datetime(["2025-01-03"]).date,  # Friday only
            data={"VTI": [250.0]},
        )
        cny = {date(2025, 1, 3): 7.25}
        # Monday requested, should walk back to Friday
        price_date, mf_price_date, cny_rate = _resolve_date_windows(
            prices, cny, date(2025, 1, 6),
        )
        assert price_date == date(2025, 1, 3)
        assert cny_rate == 7.25

    def test_raises_when_no_cny_rate(self) -> None:
        import pytest as _pytest
        prices = pd.DataFrame(
            index=pd.to_datetime(["2025-01-03"]).date,
            data={"VTI": [250.0]},
        )
        with _pytest.raises(ValueError, match="No CNY rate"):
            _resolve_date_windows(prices, {}, date(2025, 1, 3))


# ── _build_allocation_row ──────────────────────────────────────────────────


class TestBuildAllocationRow:
    ASSETS = {
        "VTI": {"category": "US Equity", "subtype": "broad"},
        "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
        "GLD": {"category": "Safe Net", "subtype": ""},
    }

    def test_categorizes_and_sums_totals(self) -> None:
        row = _build_allocation_row(
            current=date(2025, 1, 2),
            ticker_values={"VTI": 5000.0, "VXUS": 2000.0, "GLD": 1000.0},
            assets=self.ASSETS,
            cost_basis_by_ticker={},
        )
        assert row["date"] == "2025-01-02"
        assert row["total"] == 8000.0
        assert row["us_equity"] == 5000.0
        assert row["non_us_equity"] == 2000.0
        assert row["safe_net"] == 1000.0
        assert row["liabilities"] == 0

    def test_zero_values_skipped(self) -> None:
        row = _build_allocation_row(
            current=date(2025, 1, 2),
            ticker_values={"VTI": 5000.0, "VXUS": 0.0},
            assets=self.ASSETS,
            cost_basis_by_ticker={},
        )
        tickers = [t["ticker"] for t in row["tickers"]]
        assert "VXUS" not in tickers

    def test_negative_value_counts_as_liability(self) -> None:
        row = _build_allocation_row(
            current=date(2025, 1, 2),
            ticker_values={"VTI": 5000.0, "CreditCard": -1000.0},
            assets=self.ASSETS,
            cost_basis_by_ticker={},
        )
        assert row["total"] == 5000.0  # liability NOT added to total
        assert row["liabilities"] == -1000.0
