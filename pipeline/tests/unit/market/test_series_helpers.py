"""Tests for etl.market._series helpers."""

from __future__ import annotations

from datetime import date

import pandas as pd

from etl.market._series import (
    forward_fill_prices_by_date,
    resample_daily_to_monthly,
    to_monthly_records,
)


class TestToMonthlyRecords:
    def test_flattens_series_to_records(self) -> None:
        dates = pd.to_datetime(["2025-01-31", "2025-02-28", "2025-03-31"])
        series = pd.Series([100.123, 101.456, 102.999], index=dates)

        result = to_monthly_records(series)
        assert result == [
            {"date": "2025-01", "value": 100.12},
            {"date": "2025-02", "value": 101.46},
            {"date": "2025-03", "value": 103.00},
        ]

    def test_skips_nan_values(self) -> None:
        dates = pd.to_datetime(["2025-01-31", "2025-02-28", "2025-03-31"])
        series = pd.Series([100.0, float("nan"), 102.0], index=dates)

        result = to_monthly_records(series)
        assert result == [
            {"date": "2025-01", "value": 100.00},
            {"date": "2025-03", "value": 102.00},
        ]

    def test_empty_series(self) -> None:
        assert to_monthly_records(pd.Series(dtype=float)) == []


class TestResampleDailyToMonthly:
    def test_takes_month_end_last_value(self) -> None:
        dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-27", "2025-02-28"])
        series = pd.Series([1.0, 2.0, 3.0, 4.0], index=dates)

        result = resample_daily_to_monthly(series)
        assert list(result.index.strftime("%Y-%m-%d")) == ["2025-01-31", "2025-02-28"]
        assert list(result.values) == [2.0, 4.0]

    def test_empty_series_returns_empty(self) -> None:
        empty = resample_daily_to_monthly(pd.Series(dtype=float))
        assert empty.empty

    def test_all_nan_returns_empty(self) -> None:
        dates = pd.to_datetime(["2025-01-31", "2025-02-28"])
        series = pd.Series([float("nan"), float("nan")], index=dates)

        result = resample_daily_to_monthly(series)
        assert result.empty


class TestForwardFillPricesByDate:
    def test_basic_shape(self) -> None:
        rows = [
            ("SPY", date(2025, 1, 1), 100.0),
            ("SPY", date(2025, 1, 3), 102.0),
            ("QQQ", date(2025, 1, 2), 400.0),
        ]
        result = forward_fill_prices_by_date(rows)

        # Every observed date gets an entry.
        assert set(result.keys()) == {date(2025, 1, 1), date(2025, 1, 2), date(2025, 1, 3)}
        # SPY carries forward on 1/2 where it wasn't observed; QQQ carries to 1/3.
        assert result[date(2025, 1, 1)] == {"SPY": 100.0}
        assert result[date(2025, 1, 2)] == {"SPY": 100.0, "QQQ": 400.0}
        assert result[date(2025, 1, 3)] == {"SPY": 102.0, "QQQ": 400.0}

    def test_symbol_not_yet_traded_is_absent(self) -> None:
        # QQQ first observation is 1/3 — no carry backwards.
        rows = [
            ("SPY", date(2025, 1, 1), 100.0),
            ("QQQ", date(2025, 1, 3), 400.0),
        ]
        result = forward_fill_prices_by_date(rows)
        assert result[date(2025, 1, 1)] == {"SPY": 100.0}
        assert "QQQ" not in result[date(2025, 1, 1)]
        assert result[date(2025, 1, 3)] == {"SPY": 100.0, "QQQ": 400.0}

    def test_unsorted_input_still_correct(self) -> None:
        rows = [
            ("SPY", date(2025, 1, 3), 102.0),
            ("QQQ", date(2025, 1, 2), 400.0),
            ("SPY", date(2025, 1, 1), 100.0),
        ]
        result = forward_fill_prices_by_date(rows)
        assert result[date(2025, 1, 3)] == {"SPY": 102.0, "QQQ": 400.0}

    def test_empty_input(self) -> None:
        assert forward_fill_prices_by_date([]) == {}
