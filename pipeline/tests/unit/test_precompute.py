"""Tests for daily + prefix sum pre-computation."""
from __future__ import annotations

from datetime import date

from generate_asset_snapshot.precompute import compute_daily_series, compute_prefix_sums


class TestComputeDailySeries:
    def test_returns_list_of_dicts(self) -> None:
        snapshots = {
            date(2025, 1, 2): {"total": 100000, "US Equity": 55000, "Non-US Equity": 15000, "Crypto": 3000, "Safe Net": 27000},
            date(2025, 1, 3): {"total": 101000, "US Equity": 55500, "Non-US Equity": 15200, "Crypto": 3100, "Safe Net": 27200},
        }
        result = compute_daily_series(snapshots)
        assert len(result) == 2
        assert result[0]["date"] == "2025-01-02"
        assert result[0]["total"] == 100000
        assert result[0]["usEquity"] == 55000
        assert result[0]["nonUsEquity"] == 15000

    def test_sorted_by_date(self) -> None:
        snapshots = {
            date(2025, 1, 3): {"total": 101000, "US Equity": 0, "Non-US Equity": 0, "Crypto": 0, "Safe Net": 0},
            date(2025, 1, 2): {"total": 100000, "US Equity": 0, "Non-US Equity": 0, "Crypto": 0, "Safe Net": 0},
        }
        result = compute_daily_series(snapshots)
        assert result[0]["date"] < result[1]["date"]

    def test_empty_input(self) -> None:
        assert compute_daily_series({}) == []


class TestComputePrefixSums:
    def test_cumulative_values(self) -> None:
        daily_flows = [
            {"date": date(2025, 1, 2), "income": 5000, "expenses": 1000, "buys": 3000, "sells": 0, "dividends": 10, "net_cash_in": 2000, "cc_payments": 500},
            {"date": date(2025, 1, 3), "income": 0, "expenses": 200, "buys": 500, "sells": 1000, "dividends": 0, "net_cash_in": 0, "cc_payments": 0},
        ]
        result = compute_prefix_sums(daily_flows)
        assert len(result) == 2
        assert result[0]["income"] == 5000
        assert result[1]["income"] == 5000  # cumulative, no new income
        assert result[1]["expenses"] == 1200  # 1000 + 200
        assert result[1]["sells"] == 1000
        assert result[1]["netCashIn"] == 2000
        assert result[1]["ccPayments"] == 500

    def test_empty_input(self) -> None:
        assert compute_prefix_sums([]) == []
