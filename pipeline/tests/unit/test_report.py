"""Tests for report building — covers activity, cashflow, balance sheet, and end-to-end."""

from __future__ import annotations

import pytest

from generate_asset_snapshot.report import build_report
from generate_asset_snapshot.types import (
    ACT_BUY,
    ACT_DEPOSIT,
    ACT_DIVIDEND,
    ACT_FOREIGN_TAX,
    ACT_INTEREST,
    ACT_REINVESTMENT,
    ACT_SELL,
    ACT_WITHDRAWAL,
    QJ_EXPENSE,
    QJ_INCOME,
    QJ_REPAYMENT,
    QJ_TRANSFER,
    FidelityTransaction,
    QianjiRecord,
)

from .conftest import load_test_config, make_portfolio

# ── Fixtures ─────────────────────────────────────────────────────────────────

REPORT_CONFIG = {
    "goal": 1_000_000,
    "assets": {
        "VOO": {"category": "US Equity", "subtype": "broad"},
        "QQQM": {"category": "US Equity", "subtype": "growth"},
        "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
        "FBTC": {"category": "Crypto"},
        "SGOV": {"category": "Safe Net"},
        "VGLT": {"category": "Safe Net"},
    },
    "target_weights": {"US Equity": 55, "Non-US Equity": 15, "Crypto": 5, "Safe Net": 25},
    "category_order": ["US Equity", "Non-US Equity", "Crypto", "Safe Net"],
    "qianji_accounts": {
        "fidelity_tracked": ["Fidelity Brokerage"],
        "cny": [],
        "credit": ["Amex Gold"],
        "ticker_map": {},
    },
}


def _txn(date: str, action: str, amount: float, symbol: str = "", desc: str = "") -> FidelityTransaction:
    return FidelityTransaction(date=date, action_type=action, amount=amount, symbol=symbol, description=desc)


def _qj(date: str, type_: str, amount: float, category: str = "", account_to: str = "", note: str = "") -> QianjiRecord:
    return QianjiRecord(date=date, type=type_, amount=amount, category=category, account_to=account_to, note=note)


@pytest.fixture()
def report_config(tmp_path):
    return load_test_config(tmp_path, REPORT_CONFIG)


# ── _build_activity ──────────────────────────────────────────────────────────


class TestBuildActivity:
    def test_categorizes_transactions(self, tmp_path, report_config):
        portfolio = make_portfolio({"VOO": 50_000, "SGOV": 10_000})
        txns = [
            _txn("03/01/2026", ACT_DEPOSIT, 5000),
            _txn("03/05/2026", ACT_BUY, -2000, "VOO"),
            _txn("03/10/2026", ACT_SELL, 1000, "SGOV"),
            _txn("03/15/2026", ACT_DIVIDEND, 50, "VOO"),
            _txn("03/20/2026", ACT_INTEREST, 10),
            _txn("03/25/2026", ACT_FOREIGN_TAX, -5),
            _txn("03/28/2026", ACT_REINVESTMENT, 20, "VOO"),
            _txn("03/30/2026", ACT_WITHDRAWAL, 500),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns, report_month="2026-03")
        a = report.activity
        assert a is not None
        assert a.net_cash_in == pytest.approx(5000 - 500)
        assert a.net_deployed == pytest.approx(2000 - 1000)
        assert a.net_passive == pytest.approx(50 + 10 - 5)
        assert a.reinvestments_total == pytest.approx(20)
        assert a.period_start == "03/01/2026"
        assert a.period_end == "03/30/2026"

    def test_filters_by_month(self, tmp_path, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        txns = [
            _txn("02/15/2026", ACT_DEPOSIT, 1000),
            _txn("03/10/2026", ACT_DEPOSIT, 2000),
            _txn("04/01/2026", ACT_DEPOSIT, 3000),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns, report_month="2026-03")
        a = report.activity
        assert a is not None
        assert a.net_cash_in == pytest.approx(2000)

    def test_buys_by_symbol_aggregated(self, tmp_path, report_config):
        portfolio = make_portfolio({"VOO": 50_000, "QQQM": 10_000})
        txns = [
            _txn("03/01/2026", ACT_BUY, -1000, "VOO"),
            _txn("03/05/2026", ACT_BUY, -500, "VOO"),
            _txn("03/10/2026", ACT_BUY, -2000, "QQQM"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns, report_month="2026-03")
        a = report.activity
        assert a is not None
        buys = {s[0]: s[2] for s in a.buys_by_symbol}
        assert buys["VOO"] == pytest.approx(1500)
        assert buys["QQQM"] == pytest.approx(2000)

    def test_dividends_by_symbol(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000, "SGOV": 10_000})
        txns = [
            _txn("03/10/2026", ACT_DIVIDEND, 50, "VOO"),
            _txn("03/15/2026", ACT_DIVIDEND, 30, "VOO"),
            _txn("03/20/2026", ACT_DIVIDEND, 10, "SGOV"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns, report_month="2026-03")
        a = report.activity
        assert a is not None
        divs = {s[0]: (s[1], s[2]) for s in a.dividends_by_symbol}
        assert divs["VOO"] == (2, pytest.approx(80))
        assert divs["SGOV"] == (1, pytest.approx(10))

    def test_activity_json_has_no_raw_lists(self, report_config):
        """After render, activity should NOT contain raw transaction lists."""
        import json

        from generate_asset_snapshot.renderers.json_renderer import render

        portfolio = make_portfolio({"VOO": 50_000})
        txns = [_txn("03/01/2026", ACT_DEPOSIT, 5000), _txn("03/05/2026", ACT_BUY, -2000, "VOO")]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns, report_month="2026-03")
        parsed = json.loads(render(report))
        act = parsed["activity"]
        for key in ("deposits", "withdrawals", "buys", "sells", "dividends"):
            assert key not in act
        # But aggregated values should be present
        assert "netCashIn" in act
        assert "buysBySymbol" in act

    def test_no_transactions_returns_none(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv")
        assert report.activity is None


# ── _build_cashflow ──────────────────────────────────────────────────────────


class TestBuildCashflow:
    def test_income_and_expenses(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 5000, "Salary"),
            _qj("2026-03-01", QJ_INCOME, 3000, "401K"),
            _qj("2026-03-05", QJ_EXPENSE, 1500, "Housing"),
            _qj("2026-03-10", QJ_EXPENSE, 200, "Meals"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        assert cf.total_income == pytest.approx(8000)
        assert cf.total_expenses == pytest.approx(1700)
        assert cf.net_cashflow == pytest.approx(6300)
        assert cf.period == "March 2026"

    def test_savings_rate(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-05", QJ_EXPENSE, 4_000, "Housing"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        assert cf.savings_rate == pytest.approx(60.0)

    def test_takehome_savings_rate_excludes_401k(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-01", QJ_INCOME, 3_000, "401K"),
            _qj("2026-03-05", QJ_EXPENSE, 5_000, "Housing"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        # Take-home = 10k (exclude 401K 3k), expenses = 5k → rate = (10k-5k)/10k = 50%
        assert cf.takehome_savings_rate == pytest.approx(50.0)

    def test_invested_tracks_fidelity_transfers(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-05", QJ_TRANSFER, 5_000, account_to="Fidelity Brokerage"),
            _qj("2026-03-10", QJ_TRANSFER, 2_000, account_to="Chase Savings"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        assert cf.invested == pytest.approx(5_000)

    def test_credit_card_payments(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-15", QJ_REPAYMENT, 3_000),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        assert cf.credit_card_payments == pytest.approx(3_000)

    def test_filters_by_month(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-02-01", QJ_INCOME, 999, "Salary"),
            _qj("2026-03-01", QJ_INCOME, 5_000, "Salary"),
            _qj("2026-04-01", QJ_INCOME, 888, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        assert cf.total_income == pytest.approx(5_000)

    def test_expense_items_sorted_descending(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-05", QJ_EXPENSE, 100, "Small"),
            _qj("2026-03-05", QJ_EXPENSE, 500, "Medium"),
            _qj("2026-03-05", QJ_EXPENSE, 2000, "Large"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        cf = report.cashflow
        assert cf is not None
        amounts = [item.amount for item in cf.expense_items]
        assert amounts == sorted(amounts, reverse=True)

    def test_no_cashflow_returns_none(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv")
        assert report.cashflow is None


# ── _build_annual_summary ────────────────────────────────────────────────────


class TestBuildAnnualSummary:
    def test_annual_expenses_by_category(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-01-05", QJ_EXPENSE, 1500, "Housing"),
            _qj("2026-02-05", QJ_EXPENSE, 1500, "Housing"),
            _qj("2026-03-05", QJ_EXPENSE, 1500, "Housing"),
            _qj("2026-01-10", QJ_EXPENSE, 200, "Meals"),
            _qj("2026-03-10", QJ_EXPENSE, 300, "Meals"),
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        annual = report.annual_summary
        assert annual is not None
        assert annual.year == 2026
        assert annual.total_expenses == pytest.approx(5000)
        cats = {item.category: item.amount for item in annual.expense_by_category}
        assert cats["Housing"] == pytest.approx(4500)
        assert cats["Meals"] == pytest.approx(500)

    def test_excludes_other_years(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2025-12-05", QJ_EXPENSE, 9999, "Old"),
            _qj("2026-01-05", QJ_EXPENSE, 100, "Current"),
            _qj("2026-03-01", QJ_INCOME, 5_000, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        annual = report.annual_summary
        assert annual is not None
        assert annual.total_expenses == pytest.approx(100)

    def test_no_expenses_returns_none(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [
            _qj("2026-03-01", QJ_INCOME, 5_000, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", cashflow=records, report_month="2026-03")
        assert report.annual_summary is None


# ── _extract_date ────────────────────────────────────────────────────────────


class TestExtractDate:
    def test_standard_filename(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        report = build_report(portfolio, report_config, "Portfolio_Positions_Mar-15-2026.csv")
        assert report.date == "March 15, 2026"

    def test_non_matching_filename(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        report = build_report(portfolio, report_config, "random.csv")
        # Falls back to current date
        assert "2026" in report.date


# ── build_report end-to-end ──────────────────────────────────────────────────


class TestBuildReport:
    def test_categories_correct(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        eq_names = [c.name for c in report.equity_categories]
        non_eq_names = [c.name for c in report.non_equity_categories]
        assert "US Equity" in eq_names
        assert "Non-US Equity" in eq_names
        assert "Crypto" in non_eq_names
        assert "Safe Net" in non_eq_names

    def test_category_values_sum_to_total(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        all_cats = report.equity_categories + report.non_equity_categories
        total = sum(c.value for c in all_cats)
        assert total == pytest.approx(100_000)

    def test_equity_has_subtypes(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        us_eq = next(c for c in report.equity_categories if c.name == "US Equity")
        subtype_names = [s.name for s in us_eq.subtypes]
        assert "broad" in subtype_names
        assert "growth" in subtype_names
        assert us_eq.subtypes[0].value + us_eq.subtypes[1].value == pytest.approx(us_eq.value)

    def test_non_equity_has_flat_holdings(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        crypto = next(c for c in report.non_equity_categories if c.name == "Crypto")
        assert len(crypto.holdings) == 1
        assert crypto.holdings[0].ticker == "FBTC"

    def test_goal_pct_uses_net_worth(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000, "SGOV": 10_000})
        snapshot = {"balances": {"Fidelity Brokerage": 60_000, "Amex Gold": -100}, "cny_rate": 7.0}
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv", balance_snapshot=snapshot)
        # Net worth = 60k - 100 = 59900, goal = 1M → 5.99%
        assert report.goal_pct == pytest.approx(5.99, abs=0.1)

    def test_goal_pct_without_balance_sheet(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000, "SGOV": 10_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")
        # No balance sheet → uses portfolio total: 60k / 1M = 6%
        assert report.goal_pct == pytest.approx(6.0)

    def test_deviation_calculation(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        all_cats = report.equity_categories + report.non_equity_categories
        for cat in all_cats:
            assert cat.deviation == pytest.approx(cat.pct - cat.target)

    def test_category_order_follows_config(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")

        eq_names = [c.name for c in report.equity_categories]
        non_eq_names = [c.name for c in report.non_equity_categories]
        config_eq = [c for c in REPORT_CONFIG["category_order"] if c in ("US Equity", "Non-US Equity")]
        config_non_eq = [c for c in REPORT_CONFIG["category_order"] if c in ("Crypto", "Safe Net")]
        assert eq_names == config_eq
        assert non_eq_names == config_non_eq


# ── _latest_complete_month ───────────────────────────────────────────────────


class TestLatestCompleteMonth:
    def test_uses_complete_month(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        # March has 30 records (complete), April has 3 (partial)
        records = [_qj(f"2026-03-{d:02d}", QJ_EXPENSE, 10, "Food") for d in range(1, 31)]
        records += [_qj(f"2026-04-{d:02d}", QJ_EXPENSE, 10, "Food") for d in range(1, 4)]
        report = build_report(portfolio, report_config, "test_Apr-03-2026.csv", cashflow=records)
        cf = report.cashflow
        assert cf is not None
        assert cf.period == "March 2026"

    def test_single_complete_month(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        records = [_qj(f"2026-03-{d:02d}", QJ_EXPENSE, 10, "Food") for d in range(1, 31)]
        report = build_report(portfolio, report_config, "test_Mar-30-2026.csv", cashflow=records)
        cf = report.cashflow
        assert cf is not None
        assert cf.period == "March 2026"


# ── Cross Reconciliation ─────────────────────────────────────────────────────


class TestCrossReconciliation:
    def test_matches_deposit_to_transfer(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        txns = [
            _txn("03/01/2026", ACT_DEPOSIT, 5000, desc="ELECTRONIC FUNDS TRANSFER"),
            _txn("03/15/2026", ACT_BUY, -5000, "VOO"),
        ]
        records = [
            _qj("2026-03-01", QJ_TRANSFER, 5000, account_to="Fidelity Brokerage"),
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv",
                              transactions=txns, cashflow=records, report_month="2026-03")
        xr = report.cross_reconciliation
        assert xr is not None
        assert len(xr.matched) == 1
        assert xr.matched[0].amount == pytest.approx(5000)

    def test_unmatched_deposits(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        txns = [
            _txn("03/01/2026", ACT_DEPOSIT, 5000),
            _txn("03/10/2026", ACT_DEPOSIT, 3000),
        ]
        records = [
            _qj("2026-03-01", QJ_TRANSFER, 5000, account_to="Fidelity Brokerage"),
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
        ]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv",
                              transactions=txns, cashflow=records, report_month="2026-03")
        xr = report.cross_reconciliation
        assert xr is not None
        assert len(xr.unmatched_fidelity) == 1
        assert xr.unmatched_fidelity[0]["amount"] == pytest.approx(3000)  # unmatched are dicts

    def test_no_cross_recon_without_both_sources(self, report_config):
        portfolio = make_portfolio({"VOO": 50_000})
        # Only transactions, no cashflow
        txns = [_txn("03/01/2026", ACT_DEPOSIT, 5000)]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns)
        assert report.cross_reconciliation is None


# ── Portfolio Reconciliation ─────────────────────────────────────────────────


class TestPortfolioReconciliation:
    def test_reconciliation_with_prev_totals(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "SGOV": 10_000})
        prev = {"VOO": 50_000, "SGOV": 10_000}
        txns = [_txn("03/01/2026", ACT_DEPOSIT, 5000)]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv",
                              transactions=txns, prev_totals=prev, prev_date="February 01, 2026")
        recon = report.reconciliation
        assert recon is not None
        assert recon.prev_date == "February 01, 2026"

    def test_no_reconciliation_without_prev(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000})
        txns = [_txn("03/01/2026", ACT_DEPOSIT, 5000)]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv", transactions=txns)
        assert report.reconciliation is None


# ── Full Report with All Sections ────────────────────────────────────────────


class TestFullReportIntegration:
    def test_all_optional_sections_populated(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "SGOV": 10_000})
        snapshot = {"balances": {"Fidelity Brokerage": 65_000, "Amex Gold": -50}, "cny_rate": 7.0}
        txns = [
            _txn("03/01/2026", ACT_DEPOSIT, 5000),
            _txn("03/05/2026", ACT_BUY, -5000, "VOO"),
            _txn("03/15/2026", ACT_DIVIDEND, 30, "VOO"),
        ]
        records = [
            _qj("2026-03-01", QJ_INCOME, 10_000, "Salary"),
            _qj("2026-03-05", QJ_EXPENSE, 2_000, "Housing"),
            _qj("2026-03-10", QJ_TRANSFER, 5000, account_to="Fidelity Brokerage"),
        ] + [_qj(f"2026-03-{d:02d}", QJ_EXPENSE, 10, "Food") for d in range(1, 26)]
        report = build_report(portfolio, report_config, "test_Mar-01-2026.csv",
                              transactions=txns, cashflow=records, balance_snapshot=snapshot, report_month="2026-03")

        assert report.activity is not None
        assert report.cashflow is not None
        assert report.balance_sheet is not None
        assert report.cross_reconciliation is not None
        assert report.annual_summary is not None
        assert "2026" in report.date

    def test_report_total_matches_portfolio(self, report_config):
        portfolio = make_portfolio({"VOO": 55_000, "QQQM": 10_000, "VXUS": 15_000, "FBTC": 5_000, "SGOV": 10_000, "VGLT": 5_000})
        report = build_report(portfolio, report_config, "test_Jan-01-2026.csv")
        assert report.total == pytest.approx(100_000)
        assert report.total_lots == 6
