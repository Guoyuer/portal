"""Tests for prices.py: price loading, caching, and holding periods.

Note: yfinance is mocked to avoid the build dependency in CI.
Only the DB-reading functions (load_prices, load_cny_rates, load_proxy_prices)
are tested here since they don't depend on yfinance.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest

# ── Mock yfinance before importing prices.py ────────────────────────────────

_yf_mock = MagicMock(spec=ModuleType)
sys.modules.setdefault("yfinance", _yf_mock)

from generate_asset_snapshot.db import get_connection, init_db  # noqa: E402
from generate_asset_snapshot.prices import (  # noqa: E402
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
