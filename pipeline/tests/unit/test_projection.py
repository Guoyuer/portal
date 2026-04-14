"""Unit tests for etl.projection — the price-only forward projection."""
from __future__ import annotations

from datetime import date

from etl.projection import (
    TickerRow,
    _price_ratio,
    project_one_day,
    project_range,
)


class TestPriceRatio:
    def test_direct_priced_ticker(self) -> None:
        assert _price_ratio("VTI", {"VTI": 250.0}, {"VTI": 200.0}) == 1.25

    def test_ticker_missing_today(self) -> None:
        assert _price_ratio("VTI", {}, {"VTI": 200.0}) is None

    def test_ticker_missing_yesterday(self) -> None:
        assert _price_ratio("VTI", {"VTI": 250.0}, {}) is None

    def test_401k_via_proxy(self) -> None:
        # 401k sp500 routes through VOO
        ratio = _price_ratio(
            "401k sp500", {"VOO": 400.0}, {"VOO": 380.0},
        )
        assert ratio is not None
        assert abs(ratio - 400.0 / 380.0) < 1e-9

    def test_cny_assets_inverts_rate(self) -> None:
        # Rate goes from 7.25 → 7.30: USD value of same CNY balance shrinks.
        # price_ratio = (1/7.30) / (1/7.25) = 7.25/7.30 < 1
        ratio = _price_ratio(
            "CNY Assets", {"CNY=X": 7.30}, {"CNY=X": 7.25},
        )
        assert ratio is not None
        assert abs(ratio - 7.25 / 7.30) < 1e-9

    def test_zero_previous_price(self) -> None:
        assert _price_ratio("VTI", {"VTI": 250.0}, {"VTI": 0.0}) is None


class TestProjectOneDay:
    def test_carry_forward_when_no_price(self) -> None:
        prev = [TickerRow("FZFXX", 5000.0, "Safe Net", "", 0.0)]
        row = project_one_day(prev, {}, {}, date(2026, 4, 14))
        assert row.safe_net == 5000.0
        assert row.tickers[0]["value"] == 5000.0

    def test_equity_reprice(self) -> None:
        prev = [TickerRow("VTI", 10_000.0, "US Equity", "broad", 8_000.0)]
        row = project_one_day(
            prev, {"VTI": 275.0}, {"VTI": 250.0}, date(2026, 4, 14),
        )
        # shares = 10000/250 = 40 ; new_val = 40 * 275 = 11000
        assert row.us_equity == 11_000.0
        assert row.tickers[0]["value"] == 11_000.0
        assert row.tickers[0]["gain_loss"] == 3_000.0
        assert row.tickers[0]["gain_loss_pct"] == 37.5

    def test_liability_not_counted_in_total(self) -> None:
        prev = [
            TickerRow("VTI", 10_000.0, "US Equity", "broad", 8_000.0),
            TickerRow("CreditCard", -500.0, "Liability", "", 0.0),
        ]
        row = project_one_day(
            prev, {"VTI": 250.0}, {"VTI": 250.0}, date(2026, 4, 14),
        )
        assert row.total == 10_000.0
        assert row.liabilities == -500.0

    def test_category_aggregation(self) -> None:
        prev = [
            TickerRow("VTI", 10_000.0, "US Equity", "broad", 0.0),
            TickerRow("VOO", 5_000.0, "US Equity", "broad", 0.0),
            TickerRow("FBTC", 3_000.0, "Crypto", "", 0.0),
        ]
        row = project_one_day(prev, {}, {}, date(2026, 4, 14))
        assert row.us_equity == 15_000.0
        assert row.crypto == 3_000.0
        assert row.total == 18_000.0


class TestProjectRange:
    def test_skips_weekends(self) -> None:
        # Fri 2026-04-10 → Mon 2026-04-13, skip Sat/Sun
        seed = [TickerRow("VTI", 10_000.0, "US Equity", "broad", 0.0)]
        prices = {
            date(2026, 4, 10): {"VTI": 250.0},
            date(2026, 4, 13): {"VTI": 260.0},
        }
        projected = project_range(seed, date(2026, 4, 10), date(2026, 4, 13), prices)
        # Only one weekday in the future-exclusive range: Mon 4/13.
        assert len(projected) == 1
        assert projected[0].date == date(2026, 4, 13)
        assert projected[0].us_equity == 10_400.0  # 40 shares × $260

    def test_multi_day_compounds(self) -> None:
        seed = [TickerRow("VTI", 10_000.0, "US Equity", "broad", 0.0)]
        prices = {
            date(2026, 4, 13): {"VTI": 250.0},
            date(2026, 4, 14): {"VTI": 260.0},
            date(2026, 4, 15): {"VTI": 270.0},
        }
        projected = project_range(seed, date(2026, 4, 13), date(2026, 4, 15), prices)
        assert len(projected) == 2
        # 4/14: 10000/250 * 260 = 10400
        assert projected[0].us_equity == 10_400.0
        # 4/15: 10400/260 * 270 = 10800 (reprice from projected 4/14 state)
        assert projected[1].us_equity == 10_800.0

    def test_empty_when_no_weekdays(self) -> None:
        # Sat to Sun — no weekdays in the (exclusive) forward window
        seed = [TickerRow("VTI", 10_000.0, "US Equity", "broad", 0.0)]
        assert project_range(seed, date(2026, 4, 11), date(2026, 4, 12), {}) == []
