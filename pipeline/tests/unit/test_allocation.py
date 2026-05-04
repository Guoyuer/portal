"""Tests for compute_daily_allocation."""
from __future__ import annotations

import logging
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

if "yfinance" not in sys.modules:
    _yf = MagicMock(spec=ModuleType, __name__="yfinance", __path__=[])
    _yf.download = MagicMock(return_value=MagicMock(empty=True))
    sys.modules["yfinance"] = _yf

from etl.allocation import (  # noqa: E402
    _add_qianji_balances,
    _build_allocation_row,
    _categorize_ticker,
    _find_price_date,
    _resolve_date_windows,
    compute_daily_allocation,
)
from etl.db import init_db  # noqa: E402
from tests.fixtures import connected_db, insert_close, insert_fidelity_txn  # noqa: E402

# ── Fixtures ────────────────────────────────────────────────────────────────


def _init_timemachine(db_path: Path) -> None:
    init_db(db_path)
    with connected_db(db_path) as conn:
        for symbol, quantity, amount in [("VTI", 10, -2500), ("VXUS", 5, -300)]:
            insert_fidelity_txn(
                conn,
                run_date="2025-01-02",
                account_number="Z29133576",
                action="YOU BOUGHT",
                action_type="buy",
                action_kind="buy",
                symbol=symbol,
                quantity=quantity,
                amount=amount,
            )
        for dt in ("2025-01-02", "2025-01-03", "2025-01-06"):
            for symbol, close in [("VTI", 250.0), ("VXUS", 60.0), ("CNY=X", 7.25)]:
                insert_close(conn, symbol, dt, close)


@contextmanager
def _qianji_conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(db_path))
    try:
        _create_qianji_schema(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _init_qianji(db_path: Path) -> None:
    with _qianji_conn(db_path) as conn:
        conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('HYSA', 5000.0, 'USD')")
        conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Alipay', 10000.0, 'CNY')")


def _make_config() -> dict:
    return {
        "assets": {
            "VTI": {"category": "US Equity", "subtype": "broad"},
            "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
            "HYSA": {"category": "Safe Net", "subtype": ""},
            "CNY Cash": {"category": "Safe Net", "subtype": ""},
        },
        "qianji_accounts": {
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


# ── compute_daily_allocation ──────────────────────────────────────────────


class TestComputeDailyAllocation:
    def test_basic_allocation(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths)

        assert len(results) == 1
        day = results[0]
        assert day["date"] == "2025-01-02"
        total = day["total"]
        assert isinstance(total, float)
        assert total > 0
        assert day["us_equity"] > 0
        assert day["non_us_equity"] > 0
        assert day["safe_net"] > 0

    def test_skips_weekends(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths, end=date(2025, 1, 6))
        result_dates = [r["date"] for r in results]
        assert "2025-01-04" not in result_dates
        assert "2025-01-05" not in result_dates
        assert "2025-01-02" in result_dates
        assert "2025-01-03" in result_dates
        assert "2025-01-06" in result_dates

    def test_ticker_detail(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths)
        tickers = results[0]["tickers"]
        ticker_names = {t["ticker"] for t in tickers}
        assert "VTI" in ticker_names
        assert "VXUS" in ticker_names

        vti = next(t for t in tickers if t["ticker"] == "VTI")
        assert vti["value"] == 2500.0
        assert vti["category"] == "US Equity"

    def test_unknown_fidelity_account_falls_back_to_fzfxx(self, tmp_path: Path) -> None:
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"

        with _qianji_conn(qj_path):
            pass

        init_db(db_path)
        with connected_db(db_path) as conn:
            insert_fidelity_txn(
                conn,
                run_date="2025-01-02",
                account_number="UNKNOWN999",
                action="DEPOSIT",
                action_type="deposit",
                action_kind="deposit",
                amount=1000,
            )
            insert_close(conn, "CNY=X", "2025-01-02", 7.25)

        config = {
            "assets": {"FZFXX": {"category": "Safe Net", "subtype": ""}},
            "qianji_accounts": {"ticker_map": {}},
            "fidelity_accounts": {},
        }

        results = _compute_allocation((db_path, qj_path), config=config)

        assert len(results) == 1
        tickers = {t["ticker"]: t["value"] for t in results[0]["tickers"]}
        assert "FZFXX" in tickers
        assert tickers["FZFXX"] == 1000.0

    def test_401k_values_included(self, allocation_paths: tuple[Path, Path]) -> None:
        db_path, _ = allocation_paths

        with connected_db(db_path) as conn:
            conn.execute("INSERT INTO empower_snapshots (snapshot_date) VALUES ('2025-01-02')")
            sid = conn.execute(
                "SELECT id FROM empower_snapshots WHERE snapshot_date = '2025-01-02'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO empower_funds (snapshot_id, cusip, ticker, shares, price, mktval)"
                " VALUES (?, '856917729', '401k sp500', 100.0, 500.0, 50000.0)",
                (sid,),
            )

        config = _make_config()
        config["assets"]["401k sp500"] = {"category": "US Equity", "subtype": "retirement"}

        results = _compute_allocation(allocation_paths, config=config)
        assert results[0]["us_equity"] >= 50000

    def test_cny_conversion(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths)
        tickers = results[0]["tickers"]
        cny = next((t for t in tickers if t["ticker"] == "CNY Cash"), None)
        assert cny is not None
        assert 1370 < cny["value"] < 1390

    def test_liabilities_tracked(self, tmp_path: Path) -> None:
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        _init_timemachine(db_path)

        with _qianji_conn(qj_path) as conn:
            conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Credit Card', -2000.0, 'USD')")

        config = _make_config()

        results = _compute_allocation((db_path, qj_path), config=config)
        assert results[0]["liabilities"] < 0

    def test_empty_range(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths, start=date(2025, 1, 10))
        assert results == []

    def test_no_qianji_db(self, tmp_path: Path) -> None:
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
        assert results[0]["us_equity"] > 0

    def test_multiple_days_replays_qianji(self, allocation_paths: tuple[Path, Path]) -> None:
        results = _compute_allocation(allocation_paths, end=date(2025, 1, 6))
        assert len(results) == 3
        totals = [r["total"] for r in results]
        assert all(t > 0 for t in totals)

    def test_qianji_late_evening_local_transaction_applies_same_day(self, tmp_path: Path) -> None:
        db_path = tmp_path / "timemachine.db"
        qj_path = tmp_path / "qianji.db"
        init_db(db_path)

        with connected_db(db_path) as conn:
            for dt in ("2025-02-28", "2025-03-03"):
                insert_close(conn, "CNY=X", dt, 7.25)

        with _qianji_conn(qj_path) as conn:
            conn.execute("INSERT INTO user_asset (name, money, currency) VALUES ('Checking', 1100.0, 'USD')")
            late_local = datetime(2025, 3, 3, 20, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles")).timestamp()
            conn.execute(
                "INSERT INTO user_bill (time, type, money, fromact, status) VALUES (?, 1, 100.0, 'Checking', 1)",
                (late_local,),
            )

        config = {
            "assets": {"Cash": {"category": "Safe Net", "subtype": ""}},
            "qianji_accounts": {"ticker_map": {"Checking": "Cash"}},
        }

        rows = compute_daily_allocation(db_path, qj_path, config, date(2025, 2, 28), date(2025, 3, 3))

        values = {row["date"]: row["safe_net"] for row in rows}
        assert values == {
            "2025-02-28": 1000.0,
            "2025-03-03": 1100.0,
        }


# ── _find_price_date ───────────────────────────────────────────────────────


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

    @pytest.mark.parametrize(
        ("ticker", "value", "expected"),
        [
            ("VOO", 5500.0, {"ticker": "VOO", "value": 5500.0, "category": "US Equity", "subtype": "broad"}),
            ("Chase CC", -2000.0, {"ticker": "Chase CC", "value": -2000.0, "category": "Liability", "subtype": ""}),
        ],
        ids=["asset", "liability"],
    )
    def test_categorizes_values(self, ticker: str, value: float, expected: dict[str, object]) -> None:
        assert _categorize_ticker(ticker, value, self.ASSETS) == expected

    @pytest.mark.parametrize(
        ("ticker", "assets", "match"),
        [
            ("UNKNOWN", ASSETS, "not in config.assets"),
            ("X", {"X": {"subtype": "foo"}}, "no 'category'"),
        ],
        ids=["missing-asset", "missing-category"],
    )
    def test_invalid_config_raises(self, ticker: str, assets: dict, match: str) -> None:
        with pytest.raises(KeyError, match=match):
            _categorize_ticker(ticker, 100.0, assets)


# ── _add_qianji_balances ───────────────────────────────────────────────────


def _qianji_values(
    *,
    qj_balances: dict[str, float],
    currencies: dict[str, str] | None = None,
    ticker_map: dict[str, str] | None = None,
    assets: dict[str, dict[str, str]] | None = None,
    skip_accounts: frozenset[str] = frozenset(),
) -> dict[str, float]:
    ticker_values: dict[str, float] = {}
    _add_qianji_balances(
        ticker_values,
        qj_balances=qj_balances,
        currencies=currencies or {},
        ticker_map=ticker_map or {},
        assets=assets or {},
        cny_rate=7.25,
        skip_accounts=skip_accounts,
        warning_keys=set(),
    )
    return ticker_values


class TestAddQianjiBalances:
    @pytest.mark.parametrize(
        ("account", "ticker", "category"),
        [
            ("Alipay", "Alipay Funds", "Non-US Equity"),
            ("建行卡", "CNY Cash", "Safe Net"),
        ],
    )
    def test_mapped_cny_converts_to_usd(self, account: str, ticker: str, category: str) -> None:
        assert _qianji_values(
            qj_balances={account: 7250.0},
            currencies={account: "CNY"},
            ticker_map={account: ticker},
            assets={ticker: {"category": category}},
        ) == {ticker: 1000.0}

    def test_unmapped_cny_warns_and_excluded(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING, logger="etl.allocation"):
            ticker_values = _qianji_values(
                qj_balances={"RandomCNY": 7250.0},
                currencies={"RandomCNY": "CNY"},
            )
        assert ticker_values == {}
        warnings = [r for r in caplog.records if "RandomCNY" in r.message]
        assert len(warnings) == 1
        assert "ticker_map" in warnings[0].message
        assert "CNY" in warnings[0].message
        assert "7250" in warnings[0].message

    def test_negative_balance_treated_as_liability(self) -> None:
        assert _qianji_values(
            qj_balances={"Visa Card": -500.0},
            currencies={"Visa Card": "USD"},
        ) == {"Visa Card": -500.0}

    def test_skipped_accounts_ignored(self) -> None:
        assert _qianji_values(
            qj_balances={"Fidelity taxable": 10000.0},
            skip_accounts=frozenset({"Fidelity taxable"}),
        ) == {}


# ── _resolve_date_windows ──────────────────────────────────────────────────


class TestResolveDateWindows:
    def test_walks_back_to_nearest_price_date(self) -> None:
        prices = pd.DataFrame(
            index=pd.to_datetime(["2025-01-03"]).date,
            data={"VTI": [250.0]},
        )
        cny = {date(2025, 1, 3): 7.25}
        price_date, mf_price_date, cny_rate = _resolve_date_windows(
            prices, cny, date(2025, 1, 6),
        )
        assert price_date == date(2025, 1, 3)
        assert cny_rate == 7.25

    def test_raises_when_no_cny_rate(self) -> None:
        prices = pd.DataFrame(
            index=pd.to_datetime(["2025-01-03"]).date,
            data={"VTI": [250.0]},
        )
        with pytest.raises(ValueError, match="No CNY rate"):
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
        )
        tickers = [t["ticker"] for t in row["tickers"]]
        assert "VXUS" not in tickers

    def test_negative_value_counts_as_liability(self) -> None:
        row = _build_allocation_row(
            current=date(2025, 1, 2),
            ticker_values={"VTI": 5000.0, "CreditCard": -1000.0},
            assets=self.ASSETS,
        )
        assert row["total"] == 5000.0
        assert row["liabilities"] == -1000.0
