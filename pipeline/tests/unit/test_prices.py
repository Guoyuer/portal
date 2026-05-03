"""Tests for prices.py: price loading, caching, and holding periods."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

pytest.importorskip("yfinance", reason="yfinance required for prices module")

from etl.db import get_connection  # noqa: E402
from etl.prices import (  # noqa: E402
    SplitValidationError,
    _holding_periods_from_action_kind_rows,
    _reverse_split_factor,
    _validate_splits_against_transactions,
    fetch_and_store_cny_rates,
    fetch_and_store_prices,
    load_cny_rates,
    load_prices,
    refresh_window_start,
)
from etl.sources import ActionKind  # noqa: E402

# ── Helpers ─────────────────────────────────────────────────────────────────


def _seed_prices(db_path: Path, records: list[tuple[str, str, float]]) -> None:
    with closing(get_connection(db_path)) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO daily_close (symbol, date, close) VALUES (?, ?, ?)",
            records,
        )
        conn.commit()


def _close(db_path: Path, symbol: str, day: str) -> float:
    with closing(get_connection(db_path)) as conn:
        return conn.execute(
            "SELECT close FROM daily_close WHERE symbol=? AND date=?",
            (symbol, day),
        ).fetchone()[0]


@contextmanager
def _patched_price_download(frame: pd.DataFrame) -> Iterator[object]:
    with patch("etl.prices.fetch.yf.download") as mock_dl, \
         patch("etl.prices.fetch._build_split_factors", return_value={}):
        mock_dl.return_value = frame
        yield mock_dl


def _price_frame(data: dict[tuple[str, str], list[float]], days: list[str]) -> pd.DataFrame:
    return pd.DataFrame(data, index=pd.to_datetime(days))


def _txn(run_date: str, symbol: str, action_kind: ActionKind, quantity: float) -> tuple[str, str, str, float]:
    return (run_date, symbol, action_kind.value, quantity)


# ── load_prices ─────────────────────────────────────────────────────────────


class TestLoadPrices:
    def test_returns_dataframe_with_correct_shape(self, empty_db: Path) -> None:
        db_path = empty_db
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

    def test_forward_fills_gaps(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("VTI", "2025-01-03", 201.0),
            ("VXUS", "2025-01-02", 60.0),
        ])
        df = load_prices(db_path)
        assert df.loc[date(2025, 1, 3), "VXUS"] == 60.0

    def test_excludes_cny_rate(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("VTI", "2025-01-02", 200.0),
            ("CNY=X", "2025-01-02", 7.25),
        ])
        df = load_prices(db_path)
        assert "CNY=X" not in df.columns
        assert "VTI" in df.columns

    def test_empty_db_returns_empty_dataframe(self, empty_db: Path) -> None:
        db_path = empty_db
        df = load_prices(db_path)
        assert df.empty

    def test_sorted_by_date(self, empty_db: Path) -> None:
        db_path = empty_db
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
    def test_loads_rates_as_dict(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("CNY=X", "2025-01-03", 7.26),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 2
        assert rates[date(2025, 1, 2)] == 7.25
        assert rates[date(2025, 1, 3)] == 7.26

    def test_empty_db_returns_empty_dict(self, empty_db: Path) -> None:
        db_path = empty_db
        rates = load_cny_rates(db_path)
        assert rates == {}

    def test_only_returns_cny_rates(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2025-01-02", 7.25),
            ("VTI", "2025-01-02", 200.0),
        ])
        rates = load_cny_rates(db_path)
        assert len(rates) == 1


# ── _holding_periods_from_action_kind_rows ─────────────────────────────────


class TestHoldingPeriodsCore:
    @pytest.mark.parametrize(
        ("rows", "expected"),
        [
            pytest.param([_txn("2025-01-02", "VOO", ActionKind.BUY, 10.0)], {
                "VOO": (date(2025, 1, 2), None),
            }, id="buy-and-hold"),
            pytest.param([
                _txn("2025-01-02", "VOO", ActionKind.BUY, 10.0),
                _txn("2025-03-15", "VOO", ActionKind.SELL, -10.0),
            ], {"VOO": (date(2025, 1, 2), date(2025, 3, 15))}, id="sell-to-zero"),
            pytest.param([
                _txn("2025-01-02", "VOO", ActionKind.BUY, 10.0),
                _txn("2025-03-15", "VOO", ActionKind.SELL, -4.0),
            ], {"VOO": (date(2025, 1, 2), None)}, id="partial-sell-still-held"),
            pytest.param([
                _txn("2025-01-02", "SGOV", ActionKind.BUY, 1000.0),
                _txn("2025-06-15", "SGOV", ActionKind.REDEMPTION, -1000.0),
            ], {"SGOV": (date(2025, 1, 2), date(2025, 6, 15))}, id="redemption-closes"),
            pytest.param([_txn("2025-01-02", "SPAXX", ActionKind.REINVESTMENT, 100.0)], {}, id="money-market"),
            pytest.param([_txn("2025-01-02", "912796CR8", ActionKind.BUY, 5.0)], {}, id="cusip"),
            pytest.param([], {}, id="empty"),
        ],
    )
    def test_holding_periods(
        self,
        rows: list[tuple[str, str, str, float]],
        expected: dict[str, tuple[date, date | None]],
    ) -> None:
        assert _holding_periods_from_action_kind_rows(rows) == expected


# ── Invariant: historical daily_close rows are immutable ───────────────────


def _cny_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        {"Close": [c for _, c in rows]},
        index=pd.to_datetime([d for d, _ in rows]),
    )


class TestHistoricalImmutabilityCnyRates:
    def test_historical_gap_filled_without_touching_existing(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2023-07-05", 7.2135),
        ])

        with patch("etl.prices.fetch.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([
                ("2023-03-13", 6.9052),
                ("2023-07-05", 99.0),
            ])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), date(2026, 4, 12))

        assert _close(db_path, "CNY=X", "2023-07-05") == pytest.approx(7.2135)
        assert _close(db_path, "CNY=X", "2023-03-13") == pytest.approx(6.9052)

    def test_recent_row_NOT_refreshed(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [
            ("CNY=X", "2023-03-13", 6.90),
            ("CNY=X", "2026-04-10", 7.20),
        ])
        end = date(2026, 4, 12)

        with patch("etl.prices.fetch.yf.download") as mock_dl:
            mock_dl.return_value = _cny_df([("2026-04-10", 7.25)])
            fetch_and_store_cny_rates(db_path, date(2023, 3, 13), end)

        assert _close(db_path, "CNY=X", "2026-04-10") == pytest.approx(7.20)
        assert date.fromisoformat(mock_dl.call_args.kwargs["start"]) == refresh_window_start(end)


class TestHistoricalImmutabilityPrices:
    def test_historical_price_preserved_when_yahoo_returns_different_value(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_prices(db_path, [("VOO", "2024-01-15", 440.50)])

        with _patched_price_download(_price_frame({("Close", "VOO"): [999.0]}, ["2024-01-15"])) as mock_dl:
            fetch_and_store_prices(
                db_path,
                {"VOO": (date(2024, 1, 15), None)},
                date(2026, 4, 12),
            )
            assert mock_dl.called

        assert _close(db_path, "VOO", "2024-01-15") == pytest.approx(440.50)


class TestFetchGateRefreshesRecentWindow:
    def test_fetch_uses_refresh_window_not_full_history(self, empty_db: Path) -> None:
        from etl.prices import refresh_window_start

        db_path = empty_db
        _seed_prices(db_path, [
            ("VOO", "2024-01-15", 440.50),
            ("VOO", "2026-04-11", 500.00),
        ])
        end = date(2026, 4, 12)

        with _patched_price_download(_price_frame({("Close", "VOO"): [505.0]}, ["2026-04-12"])) as mock_dl:
            fetch_and_store_prices(db_path, {"VOO": (date(2024, 1, 15), None)}, end)
            assert mock_dl.called
            start_d = date.fromisoformat(mock_dl.call_args.kwargs["start"])
            assert start_d == refresh_window_start(end)

    def test_fetch_triggered_when_cache_missing_entirely(self, empty_db: Path) -> None:
        db_path = empty_db

        with _patched_price_download(_price_frame({("Close", "NEW"): [100.0]}, ["2026-04-12"])) as mock_dl:
            fetch_and_store_prices(
                db_path, {"NEW": (date(2026, 4, 1), None)}, date(2026, 4, 12),
            )
            assert mock_dl.called
            assert date.fromisoformat(mock_dl.call_args.kwargs["start"]) == date(2026, 4, 1)


# ── Split cross-validation ─────────────────────────────────────────────────


def _seed_fidelity_txn(
    db_path: Path,
    run_date: str,
    symbol: str,
    action_kind: str,
    quantity: float,
) -> None:
    with closing(get_connection(db_path)) as conn:
        conn.execute(
            "INSERT INTO fidelity_transactions"
            " (run_date, account_number, action, action_kind, symbol, quantity)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (run_date, "Z29", f"TEST {action_kind} ({symbol})", action_kind, symbol, quantity),
        )
        conn.commit()


def _validate_splits(
    db_path: Path,
    periods: dict[str, tuple[date, date | None]],
    factors: dict[str, list[tuple[date, float]]],
    *,
    today: date = date(2024, 12, 1),
) -> None:
    with closing(get_connection(db_path)) as conn:
        _validate_splits_against_transactions(conn, periods, factors, today=today)


class TestReverseSplitFactor:
    @pytest.mark.parametrize(
        ("target", "factors", "expected"),
        [
            (date(2024, 1, 1), [], 1.0),
            (date(2024, 10, 10), [(date(2024, 10, 11), 3.0)], 3.0),
            (date(2024, 10, 11), [(date(2024, 10, 11), 3.0)], 1.0),
            (date(2024, 10, 12), [(date(2024, 10, 11), 3.0)], 1.0),
            (date(2024, 5, 31), [(date(2024, 6, 1), 0.1)], 0.1),
            (date(2024, 6, 1), [(date(2024, 6, 1), 0.1)], 1.0),
            (date(2021, 12, 31), [(date(2022, 1, 1), 2.0), (date(2024, 1, 1), 3.0)], 6.0),
            (date(2023, 6, 1), [(date(2022, 1, 1), 2.0), (date(2024, 1, 1), 3.0)], 3.0),
            (date(2024, 1, 2), [(date(2022, 1, 1), 2.0), (date(2024, 1, 1), 3.0)], 1.0),
        ],
    )
    def test_factor_for_date(self, target: date, factors: list[tuple[date, float]], expected: float) -> None:
        assert _reverse_split_factor(target, factors) == pytest.approx(expected)


class TestSplitCrossValidation:
    def test_matching_split_passes(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 54.044)
        _validate_splits(
            db_path,
            {"SCHD": (date(2024, 7, 8), None)},
            {"SCHD": [(date(2024, 10, 11), 3.0)]},
        )

    def test_missing_distribution_row_raises(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        with pytest.raises(SplitValidationError, match="SCHD"):
            _validate_splits(
                db_path,
                {"SCHD": (date(2024, 7, 8), None)},
                {"SCHD": [(date(2024, 10, 11), 3.0)]},
            )

    def test_wrong_distribution_qty_raises(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 20.0)
        with pytest.raises(SplitValidationError, match="SCHD.*expected.*54"):
            _validate_splits(
                db_path,
                {"SCHD": (date(2024, 7, 8), None)},
                {"SCHD": [(date(2024, 10, 11), 3.0)]},
            )

    def test_distribution_without_yahoo_split_raises(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-10-11", "SCHD", "distribution", 54.044)
        with pytest.raises(SplitValidationError, match="SCHD 2024-10-11"):
            _validate_splits(
                db_path,
                {"SCHD": (date(2024, 7, 8), None)},
                {},
            )

    def test_split_outside_holding_period_ignored(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-11-27", "NVDA", "buy", 3.0)
        _validate_splits(
            db_path,
            {"NVDA": (date(2024, 11, 27), None)},
            {"NVDA": [(date(2024, 6, 10), 10.0)]},
        )

    def test_reverse_split_pair_passes(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2023-01-15", "BOGUS", "buy", 100.0)
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "redemption", -100.0)
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "distribution", 10.0)
        _validate_splits(
            db_path,
            {"BOGUS": (date(2023, 1, 15), None)},
            {"BOGUS": [(date(2024, 6, 1), 0.1)]},
        )

    def test_reverse_split_with_missing_redemption_raises(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2023-01-15", "BOGUS", "buy", 100.0)
        _seed_fidelity_txn(db_path, "2024-06-01", "BOGUS", "distribution", 10.0)
        with pytest.raises(SplitValidationError, match="BOGUS.*expected.*-90"):
            _validate_splits(
                db_path,
                {"BOGUS": (date(2023, 1, 15), None)},
                {"BOGUS": [(date(2024, 6, 1), 0.1)]},
            )

    def test_multi_mismatch_report_includes_all(self, empty_db: Path) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2024-07-08", "SCHD", "buy", 27.022)
        _seed_fidelity_txn(db_path, "2024-07-08", "AVGO", "buy", 1.007)
        with pytest.raises(SplitValidationError) as exc:
            _validate_splits(
                db_path,
                {
                    "SCHD": (date(2024, 7, 8), None),
                    "AVGO": (date(2024, 7, 8), None),
                },
                {
                    "SCHD": [(date(2024, 10, 11), 3.0)],
                    "AVGO": [(date(2024, 7, 15), 10.0)],
                },
            )
        msg = str(exc.value)
        assert "SCHD" in msg
        assert "AVGO" in msg

    def test_same_day_split_and_special_stock_distribution_reported(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2025-06-10", "VOO", "buy", 100.0)
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 100.0)
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 5.0)
        with pytest.raises(SplitValidationError) as exc:
            _validate_splits(
                db_path,
                {"VOO": (date(2025, 6, 10), None)},
                {"VOO": [(date(2025, 12, 15), 2.0)]},
                today=date(2026, 1, 1),
            )
        msg = str(exc.value)
        assert "expected DISTRIBUTION+REDEMPTION net" not in msg
        assert "VOO 2025-12-15" in msg
        assert "split delta matched" in msg
        assert "extra DISTRIBUTION qty+=5" in msg
        assert "no matching Yahoo split" not in msg

    def test_same_day_split_matching_exactly_still_passes(
        self, empty_db: Path,
    ) -> None:
        db_path = empty_db
        _seed_fidelity_txn(db_path, "2025-06-10", "VOO", "buy", 100.0)
        _seed_fidelity_txn(db_path, "2025-12-15", "VOO", "distribution", 100.0)
        _validate_splits(
            db_path,
            {"VOO": (date(2025, 6, 10), None)},
            {"VOO": [(date(2025, 12, 15), 2.0)]},
            today=date(2026, 1, 1),
        )


# ── Cached fetch regression ────────────────────────────────────────────────


class TestCachedFetch:
    def test_cached_lo_after_global_start_does_not_trigger_full_batch(
        self, empty_db: Path,
    ) -> None:
        from etl.prices import refresh_window_start

        db_path = empty_db
        _seed_prices(db_path, [
            ("VOO", "2023-03-13", 380.0),
            ("VOO", "2026-04-11", 500.0),
            ("FBTC", "2024-01-11", 30.0),
            ("FBTC", "2026-04-11", 85.0),
        ])
        end = date(2026, 4, 12)

        with _patched_price_download(
            _price_frame({("Close", "VOO"): [505.0], ("Close", "FBTC"): [86.0]}, ["2026-04-12"])
        ) as mock_dl:
            fetch_and_store_prices(
                db_path,
                {
                    "VOO": (date(2023, 3, 13), None),
                    "FBTC": (date(2024, 1, 11), None),
                },
                end,
                global_start=date(2023, 3, 13),
            )
            assert mock_dl.called
            start_d = date.fromisoformat(mock_dl.call_args.kwargs["start"])
            assert start_d == refresh_window_start(end), (
                f"expected {refresh_window_start(end)}, got {start_d} — the "
                f"IPO'd-late symbol must not drag batch_start back to global_start"
            )
