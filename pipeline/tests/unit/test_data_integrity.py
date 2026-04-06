"""Data integrity tests — ensure manual values flow correctly through the pipeline."""

from __future__ import annotations

import pytest

from generate_asset_snapshot.config import manual_values_from_snapshot
from generate_asset_snapshot.portfolio import load_portfolio
from generate_asset_snapshot.report import build_report

from .conftest import load_test_config, write_csv

# ── Config with ticker_map (mirrors production structure) ────────────────────

FULL_CONFIG = {
    "goal": 1_000_000,
    "assets": {
        "VOO": {"category": "US Equity", "subtype": "broad"},
        "SGOV": {"category": "Safe Net"},
        "Robinhood": {"category": "US Equity", "subtype": "broad"},
        "Alipay Funds": {"category": "Non-US Equity", "subtype": "broad"},
        "CNY Assets": {"category": "Safe Net"},
        "Debit Cash": {"category": "Safe Net"},
        "I Bonds": {"category": "Safe Net"},
    },
    "target_weights": {"US Equity": 55, "Non-US Equity": 15, "Safe Net": 30},
    "category_order": ["US Equity", "Non-US Equity", "Safe Net"],
    "qianji_accounts": {
        "fidelity_tracked": ["Fidelity Brokerage"],
        "cny": ["建行卡", "Alipay Funds"],
        "credit": ["Amex Gold"],
        "ticker_map": {
            "Chase Debit": "Debit Cash",
            "I bond": "I Bonds",
            "Robinhood": "Robinhood",
            "Alipay Funds": "Alipay Funds",
        },
    },
}

SNAPSHOT = {
    "balances": {
        "Fidelity Brokerage": 100_000,
        "Chase Debit": 5_000,
        "I bond": 20_000,
        "Robinhood": 3_000,
        "Alipay Funds": 70_000,  # CNY
        "建行卡": 30_000,  # CNY, not in ticker_map
        "Amex Gold": -100,
    },
    "cny_rate": 7.0,
}


@pytest.fixture()
def full_config(tmp_path):
    return load_test_config(tmp_path, FULL_CONFIG)


class TestManualValuesFromSnapshot:
    def test_usd_accounts_mapped(self, full_config):
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        assert manual["Debit Cash"] == 5_000
        assert manual["I Bonds"] == 20_000
        assert manual["Robinhood"] == 3_000

    def test_cny_account_converted(self, full_config):
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        assert manual["Alipay Funds"] == pytest.approx(70_000 / 7.0)

    def test_cny_aggregate_excludes_ticker_mapped(self, full_config):
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        # Only 建行卡 (30k CNY) should be in CNY Assets, not Alipay Funds
        assert manual["CNY Assets"] == pytest.approx(30_000 / 7.0)

    def test_fidelity_accounts_excluded(self, full_config):
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        assert "Fidelity Brokerage" not in manual

    def test_credit_accounts_excluded(self, full_config):
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        assert "Amex Gold" not in manual


class TestManualValuesInPortfolio:
    def test_manual_values_in_portfolio_total(self, tmp_path, full_config):
        csv_path = write_csv(tmp_path, [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        portfolio = load_portfolio(csv_path, full_config, manual_values=manual)

        # Total = Fidelity (60k) + manual entries
        assert portfolio["total"] > 60_000
        assert portfolio["totals"]["Robinhood"] == 3_000
        assert portfolio["totals"]["Alipay Funds"] == pytest.approx(10_000)
        assert portfolio["totals"]["CNY Assets"] == pytest.approx(30_000 / 7.0)

    def test_portfolio_without_manual_has_no_manual_tickers(self, tmp_path, full_config):
        csv_path = write_csv(tmp_path, [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        portfolio = load_portfolio(csv_path, full_config)
        assert "Robinhood" not in portfolio["totals"]
        assert portfolio["total"] == 60_000


class TestCategoriesIncludeAllAssets:
    def test_categories_cover_manual_entries(self, tmp_path, full_config):
        csv_path = write_csv(tmp_path, [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        portfolio = load_portfolio(csv_path, full_config, manual_values=manual)
        report = build_report(portfolio, full_config, "test_Jan-01-2026.csv", balance_snapshot=SNAPSHOT)

        all_cats = report.equity_categories + report.non_equity_categories
        cat_total = sum(c.value for c in all_cats)

        # Categories must cover entire portfolio
        assert cat_total == pytest.approx(portfolio["total"])

        # Check specific categories include manual tickers
        us_eq = next(c for c in all_cats if c.name == "US Equity")
        assert us_eq.value >= 53_000  # VOO 50k + Robinhood 3k

        safe_net = next(c for c in all_cats if c.name == "Safe Net")
        assert safe_net.value >= 30_000  # SGOV 10k + Debit Cash 5k + I Bonds 20k + CNY Assets ~4.3k

    def test_balance_sheet_excludes_ticker_map_accounts(self, tmp_path, full_config):
        csv_path = write_csv(tmp_path, [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        portfolio = load_portfolio(csv_path, full_config, manual_values=manual)
        report = build_report(portfolio, full_config, "test_Jan-01-2026.csv", balance_snapshot=SNAPSHOT)

        bs = report.balance_sheet
        assert bs is not None
        account_names = [a.name for a in bs.accounts]

        # ticker_map accounts should NOT appear in balance sheet
        assert "Chase Debit" not in account_names
        assert "I bond" not in account_names
        assert "Robinhood" not in account_names
        assert "Alipay Funds" not in account_names

        # Non-ticker_map CNY account should still appear
        assert "建行卡" in account_names

    def test_net_worth_consistent(self, tmp_path, full_config):
        csv_path = write_csv(tmp_path, [
            {"Symbol": "VOO", "Description": "VOO", "Current Value": "$50,000.00"},
            {"Symbol": "SGOV", "Description": "SGOV", "Current Value": "$10,000.00"},
        ])
        manual = manual_values_from_snapshot(SNAPSHOT, full_config)
        portfolio = load_portfolio(csv_path, full_config, manual_values=manual)
        report = build_report(portfolio, full_config, "test_Jan-01-2026.csv", balance_snapshot=SNAPSHOT)

        bs = report.balance_sheet
        assert bs is not None

        # Net worth = total assets - liabilities
        assert bs.net_worth == pytest.approx(bs.total_assets - bs.total_liabilities)
        # Total assets = fidelity positions + remaining accounts
        assert bs.total_assets > portfolio["total"] - sum(manual.values())
