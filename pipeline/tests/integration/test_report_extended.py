"""Tests for extended build_report() with all data sources."""

from __future__ import annotations

from generate_asset_snapshot.report import build_report
from generate_asset_snapshot.types import (
    ActivityData,
    BalanceSheetData,
    CashFlowData,
    HoldingsDetailData,
    IndexReturn,
    MarketData,
    ReportSources,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

MINIMAL_CONFIG = {
    "assets": {
        "VTI": {"category": "US Equity", "subtype": "broad"},
        "VXUS": {"category": "Non-US Equity", "subtype": "broad"},
        "BTC": {"category": "Crypto"},
    },
    "weights": {"US Equity": 60, "Non-US Equity": 25, "Crypto": 15},
    "order": ["US Equity", "Non-US Equity", "Crypto"],
    "aliases": {},
    "manual": {},
    "goal": 500000,
    "qianji_accounts": {
        "fidelity_tracked": ["Fidelity Brokerage"],
        "cny": [],
        "credit": [],
        "ticker_map": {},
    },
}

MINIMAL_PORTFOLIO = {
    "totals": {"VTI": 60000.0, "VXUS": 25000.0, "BTC": 15000.0},
    "counts": {"VTI": 3, "VXUS": 2, "BTC": 1},
    "total": 100000.0,
}

SAMPLE_TRANSACTIONS = [
    {"date": "03/01/2026", "action_type": "deposit", "amount": 5000.0, "symbol": "", "description": "EFT"},
    {"date": "03/02/2026", "action_type": "buy", "amount": -3000.0, "symbol": "VTI", "quantity": 12, "price": 250.0},
    {"date": "03/02/2026", "action_type": "buy", "amount": -1500.0, "symbol": "VXUS", "quantity": 25, "price": 60.0},
    {"date": "03/10/2026", "action_type": "sell", "amount": 500.0, "symbol": "VTI", "quantity": 2, "price": 250.0},
    {"date": "03/15/2026", "action_type": "dividend", "amount": 120.0, "symbol": "VTI"},
    {"date": "03/15/2026", "action_type": "dividend", "amount": 45.0, "symbol": "VXUS"},
    {"date": "03/20/2026", "action_type": "reinvestment", "amount": 120.0, "symbol": "VTI"},
    {"date": "03/25/2026", "action_type": "interest", "amount": 8.50, "symbol": "SPAXX"},
    {"date": "03/25/2026", "action_type": "foreign_tax", "amount": -3.20, "symbol": "VXUS"},
]

SAMPLE_CASHFLOW = [
    {
        "id": "1",
        "date": "2026-03-01",
        "type": "income",
        "amount": 8000.0,
        "category": "Salary",
        "subcategory": "",
        "account_from": "Chase Checking",
        "account_to": "",
        "currency": "USD",
        "note": "",
    },
    {
        "id": "2",
        "date": "2026-03-05",
        "type": "expense",
        "amount": 1500.0,
        "category": "Housing",
        "subcategory": "",
        "account_from": "Chase Checking",
        "account_to": "",
        "currency": "USD",
        "note": "",
    },
    {
        "id": "3",
        "date": "2026-03-06",
        "type": "expense",
        "amount": 600.0,
        "category": "Meals",
        "subcategory": "",
        "account_from": "Amex Gold",
        "account_to": "",
        "currency": "USD",
        "note": "",
    },
    {
        "id": "4",
        "date": "2026-03-07",
        "type": "expense",
        "amount": 200.0,
        "category": "Transport",
        "subcategory": "",
        "account_from": "Chase Checking",
        "account_to": "",
        "currency": "USD",
        "note": "",
    },
    {
        "id": "5",
        "date": "2026-03-01",
        "type": "transfer",
        "amount": 5000.0,
        "category": "",
        "subcategory": "",
        "account_from": "Chase Checking",
        "account_to": "Fidelity Brokerage",
        "currency": "USD",
        "note": "",
    },
    {
        "id": "6",
        "date": "2026-03-10",
        "type": "repayment",
        "amount": 600.0,
        "category": "",
        "subcategory": "",
        "account_from": "Chase Checking",
        "account_to": "Amex Gold",
        "currency": "USD",
        "note": "",
    },
]


# ── Positions only (backward compat) ────────────────────────────────────────


class TestPositionsOnly:
    """build_report with no optional sources — must produce same result as before."""

    def test_core_fields_populated(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "Portfolio_Positions_Apr-02-2026.csv")
        assert report.total == 100000.0
        assert report.goal == 500000
        assert len(report.equity_categories) == 2
        assert len(report.non_equity_categories) == 1

    def test_optional_fields_none(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv")
        assert report.activity is None
        assert report.reconciliation is None
        assert report.balance_sheet is not None  # always built from portfolio
        assert report.cashflow is None
        assert report.cross_reconciliation is None
        assert report.market is None
        assert report.holdings_detail is None
        assert report.narrative is None
        assert report.alerts == []

    def test_contribution_still_works(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", contribute=5000)
        assert report.contribution is not None
        assert report.contribution.amount == 5000


# ── Positions + transactions ─────────────────────────────────────────────────


class TestWithTransactions:
    """build_report with Fidelity transaction history."""

    def test_activity_populated(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        assert report.activity is not None
        assert isinstance(report.activity, ActivityData)

    def test_activity_counts(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        a = report.activity
        assert a is not None
        assert len(a.deposits) == 1
        assert len(a.buys) == 2
        assert len(a.sells) == 1
        assert len(a.dividends) == 2
        assert len(a.withdrawals) == 0

    def test_activity_totals(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        a = report.activity
        assert a is not None
        assert a.net_cash_in == 5000.0  # deposits - withdrawals
        assert a.net_deployed == 4500.0 - 500.0  # |buys| - sells = 4500 - 500
        assert a.reinvestments_total == 120.0
        assert a.interest_total == 8.50
        assert a.foreign_tax_total == -3.20

    def test_activity_net_passive(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        a = report.activity
        assert a is not None
        # dividends + interest - |foreign_tax| = 165 + 8.50 - 3.20
        assert abs(a.net_passive - (165.0 + 8.50 - 3.20)) < 0.01

    def test_activity_period(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        a = report.activity
        assert a is not None
        assert a.period_start == "03/01/2026"
        assert a.period_end == "03/25/2026"

    def test_other_fields_still_none(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=SAMPLE_TRANSACTIONS)
        # balance_sheet is always built from portfolio (not cashflow-dependent)
        assert report.balance_sheet is not None
        assert report.cashflow is None
        assert report.cross_reconciliation is None


# ── Positions + cashflow ─────────────────────────────────────────────────────


class TestWithCashflow:
    """build_report with Qianji cashflow records."""

    def test_balance_sheet_from_portfolio(self) -> None:
        """Balance sheet is built from portfolio, not cashflow."""
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        assert report.balance_sheet is not None
        assert isinstance(report.balance_sheet, BalanceSheetData)
        # Total should match portfolio total
        assert report.balance_sheet.total_assets == 100000.0

    def test_balance_sheet_without_cashflow(self) -> None:
        """Balance sheet is always present (from portfolio), even without cashflow."""
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv")
        assert report.balance_sheet is not None
        assert report.balance_sheet.total_assets == 100000.0

    def test_cashflow_populated(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        assert report.cashflow is not None
        assert isinstance(report.cashflow, CashFlowData)

    def test_cashflow_totals(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        cf = report.cashflow
        assert cf is not None
        assert cf.total_income == 8000.0
        assert cf.total_expenses == 2300.0  # 1500 + 600 + 200
        assert cf.net_cashflow == 8000.0 - 2300.0

    def test_cashflow_investment_tracking(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        cf = report.cashflow
        assert cf is not None
        assert cf.invested == 5000.0  # transfer to Fidelity
        assert cf.credit_card_payments == 600.0

    def test_cashflow_savings_rate(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        cf = report.cashflow
        assert cf is not None
        expected = (8000.0 - 2300.0) / 8000.0 * 100
        assert abs(cf.savings_rate - expected) < 0.01

    def test_cashflow_period(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        cf = report.cashflow
        assert cf is not None
        assert cf.period == "March 2026"

    def test_activity_still_none(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=SAMPLE_CASHFLOW)
        assert report.activity is None


# ── Positions + transactions + cashflow ──────────────────────────────────────


class TestWithBothSources:
    """build_report with Fidelity history AND Qianji cashflow."""

    def test_cross_reconciliation_populated(self) -> None:
        report = build_report(
            MINIMAL_PORTFOLIO,
            MINIMAL_CONFIG,
            "test.csv",
            transactions=SAMPLE_TRANSACTIONS,
            cashflow=SAMPLE_CASHFLOW,
        )
        assert report.cross_reconciliation is not None
        assert report.activity is not None
        assert report.balance_sheet is not None
        assert report.cashflow is not None

    def test_cross_reconciliation_matches(self) -> None:
        report = build_report(
            MINIMAL_PORTFOLIO,
            MINIMAL_CONFIG,
            "test.csv",
            transactions=SAMPLE_TRANSACTIONS,
            cashflow=SAMPLE_CASHFLOW,
        )
        xr = report.cross_reconciliation
        assert xr is not None
        # Qianji has $5000 transfer to Fidelity on 03/01, Fidelity has $5000 deposit on 03/01
        assert xr.qianji_total == 5000.0
        assert xr.fidelity_total == 5000.0
        assert len(xr.matched) == 1
        assert xr.matched[0].amount == 5000.0


# ── Passthrough fields ───────────────────────────────────────────────────────


class TestPassthroughFields:
    """Fields that are passed through directly to ReportData."""

    def test_market_data_passthrough(self) -> None:
        market = MarketData(
            indices=[IndexReturn(ticker="SPY", name="S&P 500", month_return=-2.5, ytd_return=5.0, current=5200.0)],
            fed_rate=4.5,
            treasury_10y=4.2,
            cpi=3.1,
            unemployment=3.8,
            vix=22.0,
            dxy=104.5,
            usd_cny=7.25,
            gold_return=1.2,
            btc_return=-5.0,
            portfolio_month_return=-1.8,
        )
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", sources=ReportSources(market=market))
        assert report.market is market

    def test_holdings_detail_passthrough(self) -> None:
        detail = HoldingsDetailData(
            top_performers=[],
            bottom_performers=[],
            upcoming_earnings=[],
            all_stocks=[],
        )
        report = build_report(
            MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", sources=ReportSources(holdings_detail=detail)
        )
        assert report.holdings_detail is detail

    def test_narrative_passthrough(self) -> None:
        report = build_report(
            MINIMAL_PORTFOLIO,
            MINIMAL_CONFIG,
            "test.csv",
            sources=ReportSources(narrative="Market was volatile this week."),
        )
        assert report.narrative == "Market was volatile this week."

    def test_alerts_passthrough(self) -> None:
        alerts = ["BTC dropped 10%", "Rebalance needed"]
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", sources=ReportSources(alerts=alerts))
        assert report.alerts == alerts

    def test_alerts_default_empty(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv")
        assert report.alerts == []


# ── Edge cases ───────────────────────────────────────────────────────────────


class TestEdgeCases:
    """Edge cases for extended build_report."""

    def test_empty_transactions(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=[])
        # Empty list is falsy, so activity should be None
        assert report.activity is None

    def test_empty_cashflow(self) -> None:
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=[])
        assert report.balance_sheet is not None  # always from portfolio
        assert report.cashflow is None

    def test_transactions_only_dividends(self) -> None:
        txns = [
            {"date": "03/15/2026", "action_type": "dividend", "amount": 50.0, "symbol": "VTI"},
        ]
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", transactions=txns)
        a = report.activity
        assert a is not None
        assert len(a.dividends) == 1
        assert len(a.buys) == 0
        assert a.net_cash_in == 0
        assert a.net_deployed == 0
        assert a.net_passive == 50.0

    def test_cashflow_no_income(self) -> None:
        """Savings rate should be 0 when there's no income."""
        records = [
            {
                "date": "2026-03-05",
                "type": "expense",
                "amount": 100.0,
                "category": "Food",
                "account_from": "Chase",
                "currency": "USD",
            },
        ]
        report = build_report(MINIMAL_PORTFOLIO, MINIMAL_CONFIG, "test.csv", cashflow=records)
        cf = report.cashflow
        assert cf is not None
        assert cf.savings_rate == 0.0
