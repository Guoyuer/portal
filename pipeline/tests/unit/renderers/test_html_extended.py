"""Tests for extended HTML renderer — all ReportData sections."""

from __future__ import annotations

from generate_asset_snapshot.core.reconcile import (
    CrossReconciliationData,
    ReconciliationMatch,
)
from generate_asset_snapshot.renderers import html
from generate_asset_snapshot.types import (
    AccountBalance,
    ActivityData,
    BalanceSheetData,
    CashFlowData,
    CashFlowItem,
    CategoryData,
    HoldingsDetailData,
    IndexReturn,
    MarketData,
    ReportData,
    StockDetail,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _minimal_report(**overrides: object) -> ReportData:
    """Build a minimal ReportData with only core fields, then apply overrides."""
    defaults: dict[str, object] = {
        "date": "2026-04-02",
        "total": 100000.0,
        "total_lots": 6,
        "goal": 500000,
        "goal_pct": 20.0,
        "equity_categories": [
            CategoryData(
                name="US Equity",
                value=60000.0,
                lots=3,
                pct=60.0,
                target=60,
                deviation=0.0,
                is_equity=True,
                subtypes=[],
                holdings=[],
            ),
        ],
        "non_equity_categories": [
            CategoryData(
                name="Crypto",
                value=15000.0,
                lots=1,
                pct=15.0,
                target=15,
                deviation=0.0,
                is_equity=False,
                subtypes=[],
                holdings=[],
            ),
        ],
    }
    defaults.update(overrides)
    return ReportData(**defaults)  # type: ignore[arg-type]


def _sample_activity() -> ActivityData:
    return ActivityData(
        period_start="2026-03-01",
        period_end="2026-03-31",
        deposits=[
            {"date": "2026-03-01", "amount": 5000.0, "description": "EFT"},
            {"date": "2026-03-15", "amount": 3000.0, "description": "EFT"},
        ],
        withdrawals=[],
        buys=[
            {"date": "2026-03-02", "symbol": "VTI", "quantity": 12, "price": 250.0, "amount": -3000.0},
            {"date": "2026-03-02", "symbol": "VXUS", "quantity": 25, "price": 60.0, "amount": -1500.0},
        ],
        sells=[
            {"date": "2026-03-10", "symbol": "VTI", "quantity": 2, "price": 250.0, "amount": 500.0},
        ],
        dividends=[
            {"date": "2026-03-15", "symbol": "VTI", "amount": 120.0},
            {"date": "2026-03-15", "symbol": "VXUS", "amount": 45.0},
        ],
        reinvestments_total=120.0,
        interest_total=8.50,
        foreign_tax_total=-3.20,
        net_cash_in=8000.0,
        net_deployed=4000.0,
        net_passive=170.30,
        buys_by_symbol=[("VTI", 1, 3000.0), ("VXUS", 1, 1500.0)],
        dividends_by_symbol=[("VTI", 1, 120.0), ("VXUS", 1, 45.0)],
    )


def _sample_balance_sheet() -> BalanceSheetData:
    return BalanceSheetData(
        investment_total=100000.0,
        accounts=[
            AccountBalance(name="Chase Checking", balance=15000.0, currency="USD"),
            AccountBalance(name="Chase Savings", balance=30000.0, currency="USD"),
            AccountBalance(name="I Bonds", balance=10000.0, currency="USD"),
        ],
        accounts_total=55000.0,
        credit_cards=[
            AccountBalance(name="Amex Gold", balance=1200.0, currency="USD"),
            AccountBalance(name="Chase Freedom", balance=800.0, currency="USD"),
        ],
        total_liabilities=2000.0,
        total_assets=155000.0,
        net_worth=153000.0,
    )


def _sample_cashflow() -> CashFlowData:
    return CashFlowData(
        period="March 2026",
        income_items=[
            CashFlowItem(category="Salary", amount=8000.0, count=1),
            CashFlowItem(category="Freelance", amount=2000.0, count=3),
        ],
        total_income=10000.0,
        expense_items=[
            CashFlowItem(category="Housing", amount=1500.0, count=1),
            CashFlowItem(category="Meals", amount=600.0, count=15),
            CashFlowItem(category="Transport", amount=200.0, count=8),
        ],
        total_expenses=2300.0,
        net_cashflow=7700.0,
        invested=5000.0,
        credit_card_payments=600.0,
        savings_rate=77.0,
        takehome_savings_rate=60.0,
    )


def _sample_cross_reconciliation() -> CrossReconciliationData:
    return CrossReconciliationData(
        matched=[
            ReconciliationMatch(
                date_qianji="2026-03-01",
                date_fidelity="2026-03-01",
                amount=5000.0,
                qianji_note="Transfer to Fidelity",
                fidelity_desc="EFT",
            ),
        ],
        unmatched_qianji=[
            {"date": "2026-03-10", "amount": 1000.0, "note": "Missing deposit"},
        ],
        unmatched_fidelity=[
            {"date": "2026-03-20", "amount": 2000.0, "description": "Wire"},
        ],
        qianji_total=6000.0,
        fidelity_total=7000.0,
        unmatched_amount=3000.0,
    )


def _sample_market() -> MarketData:
    return MarketData(
        indices=[
            IndexReturn(ticker="SPY", name="S&P 500", month_return=-2.5, ytd_return=5.0, current=5200.0),
            IndexReturn(ticker="QQQ", name="NASDAQ", month_return=-3.1, ytd_return=3.2, current=17800.0),
        ],
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


def _sample_holdings_detail() -> HoldingsDetailData:
    return HoldingsDetailData(
        top_performers=[
            StockDetail(
                ticker="NVDA",
                month_return=12.5,
                start_value=10000.0,
                end_value=11250.0,
                pe_ratio=65.0,
                market_cap=3.2e12,
                high_52w=950.0,
                low_52w=500.0,
                vs_high=-5.0,
                next_earnings="May 28 (Wed)",
            ),
            StockDetail(
                ticker="MSFT",
                month_return=4.2,
                start_value=8000.0,
                end_value=8336.0,
                pe_ratio=35.0,
                market_cap=3.0e12,
                high_52w=430.0,
                low_52w=350.0,
                vs_high=-2.0,
                next_earnings="Apr 24 (Thu)",
            ),
        ],
        bottom_performers=[
            StockDetail(
                ticker="TSLA",
                month_return=-8.3,
                start_value=5000.0,
                end_value=4585.0,
                pe_ratio=80.0,
                market_cap=0.8e12,
                high_52w=280.0,
                low_52w=140.0,
                vs_high=-15.0,
                next_earnings="Apr 22 (Tue)",
            ),
        ],
        upcoming_earnings=[
            StockDetail(
                ticker="MSFT",
                month_return=4.2,
                start_value=8000.0,
                end_value=8336.0,
                pe_ratio=35.0,
                market_cap=3.0e12,
                high_52w=430.0,
                low_52w=350.0,
                vs_high=-2.0,
                next_earnings="Apr 24 (Thu)",
            ),
        ],
        all_stocks=[],
    )


# ── Activity section ──────────────────────────────────────────────────────────


class TestActivitySection:
    def test_activity_section_renders(self) -> None:
        report = _minimal_report(activity=_sample_activity())
        result = html.render(report)
        assert "Investment Activity" in result
        # Deposit count and amounts
        assert "5,000.00" in result
        assert "3,000.00" in result
        # Buy/sell info
        assert "VTI" in result
        assert "VXUS" in result
        # Net cash in
        assert "8,000.00" in result
        # Dividends
        assert "120.00" in result
        assert "45.00" in result

    def test_activity_section_omitted_when_none(self) -> None:
        report = _minimal_report(activity=None)
        result = html.render(report)
        assert "Investment Activity" not in result


# ── Balance sheet section ─────────────────────────────────────────────────────


class TestBalanceSheetSection:
    def test_balance_sheet_section_renders(self) -> None:
        report = _minimal_report(balance_sheet=_sample_balance_sheet())
        result = html.render(report)
        assert "Balance Sheet" in result
        assert "153,000.00" in result  # net worth
        assert "Chase Checking" in result
        assert "Chase Savings" in result
        assert "15,000.00" in result
        assert "30,000.00" in result
        assert "Amex Gold" in result
        assert "1,200.00" in result
        assert "155,000.00" in result  # total assets
        assert "2,000.00" in result  # total liabilities

    def test_balance_sheet_section_omitted_when_none(self) -> None:
        report = _minimal_report(balance_sheet=None)
        result = html.render(report)
        assert "Balance Sheet" not in result


# ── Cash flow section ─────────────────────────────────────────────────────────


class TestCashFlowSection:
    def test_cashflow_section_renders(self) -> None:
        report = _minimal_report(cashflow=_sample_cashflow())
        result = html.render(report)
        assert "Cash Flow" in result
        assert "March 2026" in result
        # Income items
        assert "Salary" in result
        assert "8,000.00" in result
        assert "Freelance" in result
        # Expense items
        assert "Housing" in result
        assert "1,500.00" in result
        assert "Meals" in result
        # Savings rate
        assert "77.0%" in result
        # Invested
        assert "5,000.00" in result

    def test_cashflow_section_omitted_when_none(self) -> None:
        report = _minimal_report(cashflow=None)
        result = html.render(report)
        # Should not have "Cash Flow" as a section header, but may have other
        # mentions from other sections — check for the specific section heading
        assert "Cash Flow" not in result


# ── Cross reconciliation section ──────────────────────────────────────────────


class TestCrossReconciliationSection:
    def test_cross_reconciliation_renders(self) -> None:
        report = _minimal_report(cross_reconciliation=_sample_cross_reconciliation())
        result = html.render(report)
        assert "Cross Reconciliation" in result
        # Matched pair
        assert "5,000.00" in result
        # Unmatched items
        assert "1,000.00" in result
        assert "2,000.00" in result
        # Totals
        assert "6,000.00" in result
        assert "7,000.00" in result

    def test_cross_reconciliation_omitted_when_none(self) -> None:
        report = _minimal_report(cross_reconciliation=None)
        result = html.render(report)
        assert "Cross Reconciliation" not in result


# ── Market section ────────────────────────────────────────────────────────────


class TestMarketSection:
    def test_market_section_renders(self) -> None:
        report = _minimal_report(market=_sample_market())
        result = html.render(report)
        assert "Market" in result
        # Index names
        assert "S&amp;P 500" in result or "S&P 500" in result
        assert "NASDAQ" in result
        # Returns (with sign)
        assert "2.5" in result  # month return for SPY
        assert "3.1" in result  # month return for QQQ / CPI
        # Macro indicators
        assert "4.5" in result  # fed rate
        assert "VIX" in result
        assert "22.0" in result  # vix value

    def test_market_section_omitted_when_none(self) -> None:
        report = _minimal_report(market=None)
        result = html.render(report)
        assert "Market Context" not in result


# ── Holdings detail section ───────────────────────────────────────────────────


class TestHoldingsDetailSection:
    def test_holdings_detail_renders(self) -> None:
        report = _minimal_report(holdings_detail=_sample_holdings_detail())
        result = html.render(report)
        assert "Holdings Detail" in result
        # Top performers
        assert "NVDA" in result
        assert "12.5" in result  # month return
        # Bottom performers
        assert "TSLA" in result
        assert "8.3" in result  # month return (absolute)
        # Earnings
        assert "Apr 24" in result or "May 28" in result

    def test_stock_deep_dive_omitted_when_none(self) -> None:
        report = _minimal_report(holdings_detail=None)
        result = html.render(report)
        # Holdings Detail (positions table) is always present
        assert "Holdings Detail" in result
        # But per-stock deep dive (top/bottom performers) should be absent
        assert "Top Performers" not in result
        assert "Bottom Performers" not in result


# ── Narrative section ─────────────────────────────────────────────────────────


class TestNarrativeSection:
    def test_narrative_renders(self) -> None:
        text = "Markets were volatile this week due to tariff concerns."
        report = _minimal_report(narrative=text)
        result = html.render(report)
        assert text in result

    def test_narrative_omitted_when_none(self) -> None:
        report = _minimal_report(narrative=None)
        result = html.render(report)
        assert "Market Narrative" not in result


# ── Alerts section ────────────────────────────────────────────────────────────


class TestAlertsSection:
    def test_alerts_render(self) -> None:
        alerts = ["BTC dropped 10% this week", "Portfolio needs rebalancing"]
        report = _minimal_report(alerts=alerts)
        result = html.render(report)
        assert "BTC dropped 10% this week" in result
        assert "Portfolio needs rebalancing" in result

    def test_alerts_omitted_when_empty(self) -> None:
        report = _minimal_report(alerts=[])
        result = html.render(report)
        assert "Alerts" not in result


# ── Full report ───────────────────────────────────────────────────────────────


class TestFullReport:
    def test_full_report_all_sections(self) -> None:
        report = _minimal_report(
            activity=_sample_activity(),
            balance_sheet=_sample_balance_sheet(),
            cashflow=_sample_cashflow(),
            cross_reconciliation=_sample_cross_reconciliation(),
            market=_sample_market(),
            holdings_detail=_sample_holdings_detail(),
            narrative="AI-generated market summary for this period.",
            alerts=["Alert: large deposit detected"],
        )
        result = html.render(report)

        # All section headers present
        assert "<!DOCTYPE html>" in result
        assert "</html>" in result
        assert "Alerts" in result
        assert "AI-generated market summary" in result
        assert "Investment Activity" in result
        assert "Balance Sheet" in result
        assert "Cash Flow" in result
        assert "Holdings Detail" in result
        assert "Category Summary" in result
        assert "Market Context" in result or "Market" in result
        assert "Holdings Detail" in result
        assert "Cross Reconciliation" in result

    def test_section_order(self) -> None:
        """Sections appear in the correct order in the output."""
        report = _minimal_report(
            activity=_sample_activity(),
            balance_sheet=_sample_balance_sheet(),
            cashflow=_sample_cashflow(),
            cross_reconciliation=_sample_cross_reconciliation(),
            market=_sample_market(),
            holdings_detail=_sample_holdings_detail(),
            narrative="Market narrative text.",
            alerts=["Alert message"],
        )
        result = html.render(report)

        # Inverted pyramid order:
        # Alerts -> Narrative -> Category Summary -> Contribution
        # -> Cash Flow -> Activity -> Balance Sheet -> Holdings Detail
        # -> Market -> Cross Reconciliation
        alert_pos = result.index("Alert message")
        narrative_pos = result.index("Market narrative text.")
        summary_pos = result.index("Category Summary")
        cashflow_pos = result.index("Cash Flow")
        activity_pos = result.index("Investment Activity")
        balance_pos = result.index("Balance Sheet")
        holdings_pos = result.index("Holdings Detail")
        market_pos = result.index("Market Context")
        recon_pos = result.index("Cross Reconciliation")

        assert alert_pos < narrative_pos
        assert narrative_pos < summary_pos
        assert summary_pos < cashflow_pos
        assert cashflow_pos < activity_pos
        assert activity_pos < balance_pos
        assert balance_pos < holdings_pos
        assert holdings_pos < market_pos
        assert market_pos < recon_pos
