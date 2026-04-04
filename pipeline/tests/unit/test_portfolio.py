"""Tests for portfolio loading from Fidelity CSV."""

from __future__ import annotations

from pathlib import Path

import pytest

from generate_asset_snapshot.portfolio import load_portfolio
from generate_asset_snapshot.types import PortfolioError

from .conftest import ALL_TICKERS_ROWS, load_test_config, write_csv


class TestLoadPortfolio:
    def test_loads_correct_totals(self, simple_csv, config):
        portfolio = load_portfolio(simple_csv, config)
        assert portfolio["totals"]["VOO"] == pytest.approx(55000.0)
        assert portfolio["totals"]["QQQM"] == pytest.approx(10000.0)

    def test_total_is_sum(self, simple_csv, config):
        portfolio = load_portfolio(simple_csv, config)
        assert portfolio["total"] == pytest.approx(100000.0)

    def test_lot_counts(self, simple_csv, config):
        portfolio = load_portfolio(simple_csv, config)
        assert portfolio["counts"]["VOO"] == 1
        assert portfolio["counts"]["FBTC"] == 1

    def test_aggregates_duplicate_tickers(self, tmp_path, config):
        rows = [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$10,000.00"},
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$5,000.00"},
            {"Symbol": "QQQM", "Description": "QQQM", "Current Value": "$1,000.00"},
            {"Symbol": "VXUS", "Description": "VXUS", "Current Value": "$1,000.00"},
            {"Symbol": "FBTC", "Description": "FBTC", "Current Value": "$1,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$1,000.00"},
            {"Symbol": "VGLT", "Description": "VGLT", "Current Value": "$1,000.00"},
        ]
        csv_path = write_csv(tmp_path, rows)
        portfolio = load_portfolio(csv_path, config)
        assert portfolio["totals"]["VOO"] == pytest.approx(15000.0)
        assert portfolio["counts"]["VOO"] == 2

    def test_alias_resolution(self, tmp_path, config_data):
        config_data["aliases"] = {"Long Name Fund": "VOO"}
        config = load_test_config(tmp_path, config_data)

        rows = [
            {"Symbol": "Long Name Fund", "Description": "Long Name Fund", "Current Value": "$1,000.00"},
            {"Symbol": "QQQM", "Description": "QQQM", "Current Value": "$1,000.00"},
            {"Symbol": "VXUS", "Description": "VXUS", "Current Value": "$1,000.00"},
            {"Symbol": "FBTC", "Description": "FBTC", "Current Value": "$1,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$1,000.00"},
            {"Symbol": "VGLT", "Description": "VGLT", "Current Value": "$1,000.00"},
        ]
        csv_path = write_csv(tmp_path, rows)
        portfolio = load_portfolio(csv_path, config)
        assert portfolio["totals"]["VOO"] == pytest.approx(1000.0)

    def test_skips_pending_activity(self, tmp_path, config):
        rows = [
            {"Symbol": "Pending Activity", "Description": "Pending Activity", "Current Value": "$999.00"},
            *ALL_TICKERS_ROWS,
        ]
        csv_path = write_csv(tmp_path, rows)
        portfolio = load_portfolio(csv_path, config)
        assert "Pending Activity" not in portfolio["totals"]

    def test_handles_dashes_in_value(self, tmp_path, config):
        rows = [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "--"},
            {"Symbol": "QQQM", "Description": "QQQM", "Current Value": "$1,000.00"},
            {"Symbol": "VXUS", "Description": "VXUS", "Current Value": "$1,000.00"},
            {"Symbol": "FBTC", "Description": "FBTC", "Current Value": "$1,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$1,000.00"},
            {"Symbol": "VGLT", "Description": "VGLT", "Current Value": "$1,000.00"},
        ]
        csv_path = write_csv(tmp_path, rows)
        portfolio = load_portfolio(csv_path, config)
        assert portfolio["totals"]["VOO"] == pytest.approx(0.0)

    def test_missing_csv(self, config):
        with pytest.raises(PortfolioError, match="CSV not found"):
            load_portfolio(Path("/nonexistent.csv"), config)

    def test_unknown_ticker_exits(self, tmp_path, config):
        rows = [{"Symbol": "UNKNOWN", "Description": "Mystery", "Current Value": "$100.00"}]
        csv_path = write_csv(tmp_path, rows)
        with pytest.raises(PortfolioError, match="not configured"):
            load_portfolio(csv_path, config)

    def test_missing_headers_exits(self, tmp_path, config):
        import csv as csv_mod

        p = tmp_path / "bad.csv"
        with p.open("w", newline="") as f:
            w = csv_mod.DictWriter(f, fieldnames=["Foo", "Bar"])
            w.writeheader()
        with pytest.raises(PortfolioError, match="Missing required CSV headers"):
            load_portfolio(p, config)
