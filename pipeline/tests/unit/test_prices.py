"""Tests for prices.py: price loading, caching, and holding periods."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

pytest.importorskip("yfinance", reason="yfinance required for prices module")

from etl.db import get_connection, init_db  # noqa: E402
from etl.prices import (  # noqa: E402
    _holding_periods_core,
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
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


# ── Invariant: historical daily_close rows are immutable ───────────────────


def _cny_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    """Build a yfinance-style single-symbol DataFrame.

    Shape matches what ``yf.download("CNY=X", ...)`` returns: a DataFrame
    indexed by DatetimeIndex with a flat "Close" column.
    """
    return pd.DataFrame(
        {"Close": [c for _, c in rows]},
        index=pd.to_datetime([d for d, _ in rows]),
    )


class TestHistoricalImmutabilityCnyRates:
    """`fetch_and_store_cny_rates` must never overwrite historical values.

    Yahoo occasionally returns partial or revised data for past dates. Once a
    rate is stored for a date older than the refresh window, it should be
    treated as the authoritative historical value. Only recent dates (within
    the refresh window) may be updated — Yahoo sometimes publishes late
    corrections for the past few days.
    """

    def test_historical_row_preserved_when_yahoo_returns_different_value(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [("CNY=X", "2023-03-13", 6.9052)])

        with patch("etl.prices.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([("2023-03-13", 99.0)])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        # Historical row unchanged
        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-03-13'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(6.9052)

    def test_historical_gap_filled_without_touching_existing(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("CNY=X", "2023-07-05", 7.2135),  # existing
        ])

        with patch("etl.prices.yf.download") as mock_dl:
            # Yahoo returns BOTH the existing date (with different value) and a
            # historical gap date.
            mock_dl.return_value = _cny_df([
                ("2023-03-13", 6.9052),  # new gap-fill
                ("2023-07-05", 99.0),    # conflict; must be ignored
            ])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        conn = get_connection(db_path)
        existing = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-07-05'",
        ).fetchone()
        gap = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2023-03-13'",
        ).fetchone()
        conn.close()
        assert existing[0] == pytest.approx(7.2135)  # preserved
        assert gap[0] == pytest.approx(6.9052)       # filled

    def test_recent_row_updated_within_refresh_window(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        # Build end is 2026-04-12; refresh window is 7 days → 2026-04-05 onward.
        _seed_prices(db_path, [("CNY=X", "2026-04-10", 7.20)])

        with patch("etl.prices.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([("2026-04-10", 7.25)])  # Yahoo correction
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' AND date='2026-04-10'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(7.25)  # refreshed


class TestHistoricalImmutabilityPrices:
    """`fetch_and_store_prices` enforces the same invariant for per-symbol prices."""

    def test_historical_price_preserved_when_yahoo_returns_different_value(
        self, tmp_path: Path,
    ) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [("VOO", "2024-01-15", 440.50)])

        # Open-ended holding period — the recent-window refresh always queues
        # a fetch, so yfinance.download will be called.
        with patch("etl.prices.yf.download") as mock_dl, \
             patch("etl.prices._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [999.0]},
                index=pd.to_datetime(["2024-01-15"]),
            )
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},  # still held → need_end = end
                date(2026, 4, 12),
            )
            # Confirm fetch actually ran (otherwise the test is a no-op).
            assert mock_dl.called

        conn = get_connection(db_path)
        r = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='VOO' AND date='2024-01-15'",
        ).fetchone()
        conn.close()
        assert r[0] == pytest.approx(440.50)  # historical value preserved


class TestFetchGateRefreshesRecentWindow:
    """Regression: the fetch gate must always refresh the recent window.

    Earlier logic skipped the fetch when ``cached_hi`` was within 4 days of
    ``need_end``, which silently left new trading days stale (observed:
    cached_hi=04-10, need_end=04-14 → skip, missing 04-13 and 04-14 closes).
    """

    def test_fetch_triggered_when_cache_is_one_day_stale(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        init_db(db_path)
        # Cache ends 2026-04-11; need_end = 2026-04-12. Old logic would skip
        # (04-11 < 04-08 is False). New logic must still fetch recent window.
        _seed_prices(db_path, [
            ("VOO", "2024-01-15", 440.50),
            ("VOO", "2026-04-11", 500.00),
        ])

        with patch("etl.prices.yf.download") as mock_dl, \
             patch("etl.prices._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [505.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},
                date(2026, 4, 12),
            )
            assert mock_dl.called

    def test_fetch_uses_refresh_window_not_full_history(self, tmp_path: Path) -> None:
        """When history is covered, fetch only the recent window (not from hp_start)."""
        db_path = tmp_path / "test.db"
        init_db(db_path)
        _seed_prices(db_path, [
            ("VOO", "2024-01-15", 440.50),
            ("VOO", "2026-04-11", 500.00),
        ])

        with patch("etl.prices.yf.download") as mock_dl, \
             patch("etl.prices._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "VOO"): [505.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},
                date(2026, 4, 12),
            )
            # start kwarg should be the refresh window, not hp_start
            assert mock_dl.called
            start_kw = mock_dl.call_args.kwargs["start"]
            assert start_kw != "2024-01-15"  # not fetching full history
            assert start_kw >= "2026-04-05"  # within REFRESH_WINDOW_DAYS=7

    def test_fetch_triggered_when_cache_missing_entirely(self, tmp_path: Path) -> None:
        """New symbol with no cache → fetch full range."""
        db_path = tmp_path / "test.db"
        init_db(db_path)

        with patch("etl.prices.yf.download") as mock_dl, \
             patch("etl.prices._build_split_factors", return_value={}):
            mock_dl.return_value = pd.DataFrame(
                {("Close", "NEW"): [100.0]},
                index=pd.to_datetime(["2026-04-12"]),
            )
            fetch_and_store_prices(
                db_path,
                {"NEW": (date(2026, 4, 1), None)},
                date(2026, 4, 12),
            )
            assert mock_dl.called
            start_kw = mock_dl.call_args.kwargs["start"]
            assert start_kw == "2026-04-01"  # full history from hp_start
