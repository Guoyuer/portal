"""Tests for analysis helpers (pct, allocation, grouping)."""

from __future__ import annotations

import pytest

from generate_asset_snapshot.analysis import (
    calculate_allocation,
    get_tickers,
    group_by_subtype,
    pct,
)
from generate_asset_snapshot.portfolio import load_portfolio
from generate_asset_snapshot.types import Config

from .conftest import make_portfolio


class TestPct:
    def test_basic(self):
        assert pct(25, 100) == pytest.approx(25.0)

    def test_zero_total(self):
        assert pct(50, 0) == 0

    def test_precision(self):
        assert pct(1, 3) == pytest.approx(33.3333, rel=1e-3)


class TestGetTickers:
    def test_returns_category_tickers_sorted_by_value(self, simple_csv, config):
        portfolio = load_portfolio(simple_csv, config)
        tickers = get_tickers(portfolio, config, "US Equity")
        assert set(tickers) == {"VOO", "QQQM"}
        assert tickers[0] == "VOO"

    def test_empty_for_missing_category(self, simple_csv, config):
        portfolio = load_portfolio(simple_csv, config)
        assert get_tickers(portfolio, config, "Nonexistent") == []


class TestGroupBySubtype:
    def test_groups_correctly(self, config):
        tickers = ["VOO", "QQQM"]
        groups = group_by_subtype(tickers, config)
        assert groups["broad"] == ["VOO"]
        assert groups["growth"] == ["QQQM"]

    def test_unknown_subtype_goes_to_other(self):
        config = Config(
            assets={"X": {"category": "Test"}},
            weights={},
            order=[],
            aliases={},
            manual={},
            goal=0,
            qianji_accounts={},
        )
        groups = group_by_subtype(["X"], config)
        assert groups["other"] == ["X"]


class TestCalculateAllocation:
    def test_total_equals_contribution(self, config):
        portfolio = make_portfolio(
            {"VOO": 40000, "QQQM": 10000, "VXUS": 15000, "FBTC": 5000, "SGOV": 20000, "VGLT": 10000}
        )
        allocation = calculate_allocation(portfolio, config, 5000.0)
        assert sum(allocation.values()) == pytest.approx(5000.0)

    def test_prioritizes_underweight(self, config):
        portfolio = make_portfolio(
            {"VOO": 35000, "QQQM": 5000, "VXUS": 15000, "FBTC": 5000, "SGOV": 20000, "VGLT": 20000}
        )
        allocation = calculate_allocation(portfolio, config, 10000.0)
        assert allocation["US Equity"] > allocation.get("Hedge", 0)
        assert allocation["US Equity"] > 0

    def test_overweight_gets_nothing_when_underweight_exists(self, config):
        portfolio = make_portfolio(
            {"VOO": 30000, "QQQM": 5000, "VXUS": 15000, "FBTC": 5000, "SGOV": 20000, "VGLT": 25000}
        )
        allocation = calculate_allocation(portfolio, config, 5000.0)
        assert allocation["Hedge"] == pytest.approx(0.0)

    def test_all_at_target_distributes_proportionally(self, config):
        portfolio = make_portfolio(
            {"VOO": 50000, "QQQM": 5000, "VXUS": 15000, "FBTC": 5000, "SGOV": 20000, "VGLT": 5000}
        )
        allocation = calculate_allocation(portfolio, config, 10000.0)
        assert allocation["US Equity"] / 10000 == pytest.approx(0.55, abs=0.05)
        assert allocation["Safe Net"] / 10000 == pytest.approx(0.20, abs=0.05)

    def test_zero_contribution(self, config):
        portfolio = make_portfolio(
            {"VOO": 50000, "QQQM": 5000, "VXUS": 15000, "FBTC": 5000, "SGOV": 20000, "VGLT": 5000}
        )
        allocation = calculate_allocation(portfolio, config, 0.0)
        assert all(v == pytest.approx(0.0) for v in allocation.values())
