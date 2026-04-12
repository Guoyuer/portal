"""Tests for prices.py: price loading, caching, and holding periods."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("yfinance", reason="yfinance required for prices module")

from generate_asset_snapshot.db import get_connection, init_db  # noqa: E402
from generate_asset_snapshot.prices import (  # noqa: E402
    _holding_periods_core,
    load_cny_rates,
    load_prices,
    load_proxy_prices,
)

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_prices(db_path: Path, records: list[tuple[str, str, float]]) -> None:
    """Insert (symbol, date, close) records into daily_close."""
    conn = get_connection(db_path)
    for sym, dt, close in records:
        conn.execute(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            (sym, dt, close),
        )
    conn.commit()
    conn.close()


# ── load_prices ─────────────────────────────────────────────────────────────


class TestLoadPrices:
    def test_returns_dataframe_with_correct_shape(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
            ("VXUS", "2025-01-02", 60.0),
            ("VXUS", "2025-01-03", 60.5),
        ])
        df = load_prices(db_path)
        assert df.shape == (2, 2)
        assert "VTI" in df.columns
        assert "VXUS" in df.columns

    def test_forward_fills_gaps(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
            ("VXUS", "2025-01-02", 60.0),
        ])
        df = load_prices(db_path)
        assert df.loc[date(2025, 1, 3), "VXUS"] == 60.0

    def test_excludes_cny_rate(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("CNY=X", "2025-01-02", 7.25),
        ])
        df = load_prices(db_path)
        assert "CNY=X" not in df.columns
        assert "VTI" in df.columns

    def test_empty_db_returns_empty_dataframe(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        df = load_prices(db_path)
        assert df.empty

    def test_sorted_by_date(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("VTI", "2025-01-05", 202.0),
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
        ])
        df = load_prices(db_path)
        dates = list(df.index)
        assert dates == sorted(dates)


# ── load_cny_rates ──────────────────────────────────────────────────────────


class TestLoadCnyRates:
    def test_loads_rates_as_dict(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("CNY=X", "2025-01-03", 7.26),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 2
        assert rates[date(2025, 1, 2)] == 7.25
        assert rates[date(2025, 1, 3)] == 7.26

    def test_empty_db_returns_empty_dict(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        rates = load_cny_rates(db_path)
        assert rates == {}

    def test_only_returns_cny_rates(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("VTI", "2025-01-02", 200.0),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 1


# ── load_proxy_prices ───────────────────────────────────────────────────────


class TestLoadProxyPrices:
    def test_loads_proxy_for_each_ticker(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("SPY", "2025-01-02", 500.0),
            ("SPY", "2025-01-03", 501.0),
            ("AGG", "2025-01-02", 100.0),
        ])
        proxy_map = {"401k sp500": "SPY", "401k bonds": "AGG"}
        result = load_proxy_prices(db_path, proxy_map)
        assert "SPY" in result
        assert "AGG" in result
        assert len(result["SPY"]) == 2
        assert result["SPY"][date(2025, 1, 2)] == 500.0

    def test_empty_proxy_map(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        result = load_proxy_prices(db_path, {})
        assert result == {}


# ── _holding_periods_core ──────────────────────────────────────────────────


class TestHoldingPeriodsCore:
    """Test the shared holding-period logic with pre-normalized tuples."""

    def test_buy_and_hold(self) -> None:
        rows = [
            ("2025-01-02", "VOO", "YOU BOUGHT X", 10.0),
        ]
        result = _holding_periods_core(rows)
        assert result["VOO"] == (date(2025, 1, 2), None)

    def test_buy_then_sell_to_zero(self) -> None:
        rows = [
            ("2025-01-02", "VOO", "YOU BOUGHT X", 10.0),
            ("2025-03-15", "VOO", "YOU SOLD X", -10.0),
        ]
        result = _holding_periods_core(rows)
        assert result["VOO"] == (date(2025, 1, 2), date(2025, 3, 15))

    def test_money_market_excluded(self) -> None:
        rows = [
            ("2025-01-02", "SPAXX", "REINVESTMENT", 100.0),
        ]
        result = _holding_periods_core(rows)
        assert "SPAXX" not in result

    def test_cusip_excluded(self) -> None:
        rows = [
            ("2025-01-02", "912796CR8", "YOU BOUGHT X", 5.0),
        ]
        result = _holding_periods_core(rows)
        assert "912796CR8" not in result

    def test_partial_sell_still_held(self) -> None:
        rows = [
            ("2025-01-02", "VOO", "YOU BOUGHT X", 10.0),
            ("2025-03-15", "VOO", "YOU SOLD X", -4.0),
        ]
        result = _holding_periods_core(rows)
        # Still held — end should be None
        assert result["VOO"] == (date(2025, 1, 2), None)

    def test_empty_rows(self) -> None:
        result = _holding_periods_core([])
        assert result == {}
