"""Tests for the JSON renderer — verifies camelCase output and data transformations."""

import json

from generate_asset_snapshot.renderers.json_renderer import render
from generate_asset_snapshot.types import (
    ActivityData,
    BalanceSheetData,
    CashFlowData,
    CashFlowItem,
    CategoryData,
    ChartData,
    HoldingData,
    MonthlyFlowPoint,
    ReportData,
    SnapshotPoint,
    SubtypeGroup,
)


def _minimal_report(**overrides) -> ReportData:
    defaults = {
        "date": "April 04, 2026",
        "total": 100000.0,
        "total_lots": 10,
        "goal": 2000000,
        "goal_pct": 5.0,
        "equity_categories": [
            CategoryData(
                name="US Equity", value=60000, lots=5, pct=60.0,
                target=55, deviation=5.0, is_equity=True,
                subtypes=[SubtypeGroup(name="broad", holdings=[], value=60000, lots=5, pct=60.0)],
                holdings=[],
            ),
        ],
        "non_equity_categories": [
            CategoryData(
                name="Safe Net", value=40000, lots=5, pct=40.0,
                target=45, deviation=-5.0, is_equity=False, subtypes=[],
                holdings=[HoldingData(ticker="SGOV", lots=5, value=40000, pct=40.0, category="Safe Net", subtype="")],
            ),
        ],
    }
    defaults.update(overrides)
    return ReportData(**defaults)  # type: ignore[arg-type]


class TestJsonOutput:
    def test_valid_json(self):
        result = render(_minimal_report())
        parsed = json.loads(result)
        assert isinstance(parsed, dict)

    def test_camel_case_keys(self):
        parsed = json.loads(render(_minimal_report()))
        assert "totalLots" in parsed
        assert "goalPct" in parsed
        assert "equityCategories" in parsed
        assert "nonEquityCategories" in parsed
        # No snake_case keys at top level
        assert "total_lots" not in parsed
        assert "goal_pct" not in parsed

    def test_nested_camel_case(self):
        parsed = json.loads(render(_minimal_report()))
        cat = parsed["equityCategories"][0]
        assert "isEquity" in cat
        assert "is_equity" not in cat

    def test_optional_sections_null(self):
        parsed = json.loads(render(_minimal_report()))
        assert parsed["activity"] is None
        assert parsed["balanceSheet"] is None
        assert parsed["cashflow"] is None
        assert parsed["chartData"] is None
        # contribution, narrative, alerts are stripped from output
        assert "contribution" not in parsed
        assert "narrative" not in parsed
        assert "alerts" not in parsed


class TestActivityStripping:
    def _report_with_activity(self) -> ReportData:
        txn = {"date": "2026-03-15", "account": "Taxable", "action_type": "buy",
               "symbol": "VOO", "description": "VANGUARD", "quantity": 1.0,
               "price": 500.0, "amount": -500.0, "raw_action": "YOU BOUGHT",
               "dedup_key": ("2026-03-15", "VOO", -500.0)}
        return _minimal_report(activity=ActivityData(
            period_start="2026-03-01", period_end="2026-03-31",
            deposits=[], withdrawals=[], buys=[txn], sells=[], dividends=[],
            reinvestments_total=0, interest_total=0, foreign_tax_total=0,
            net_cash_in=0, net_deployed=500, net_passive=0,
            buys_by_symbol=[("VOO", 1, 500.0)], dividends_by_symbol=[],
        ))

    def test_raw_transactions_stripped(self):
        parsed = json.loads(render(self._report_with_activity()))
        act = parsed["activity"]
        assert "deposits" not in act
        assert "buys" not in act
        assert "sells" not in act
        assert "dividends" not in act
        assert "withdrawals" not in act

    def test_aggregations_preserved(self):
        parsed = json.loads(render(self._report_with_activity()))
        act = parsed["activity"]
        assert act["buysBySymbol"] == [["VOO", 1, 500.0]]
        assert act["netDeployed"] == 500
        assert act["periodStart"] == "2026-03-01"


class TestBalanceSheet:
    def _report_with_bs(self) -> ReportData:
        return _minimal_report(balance_sheet=BalanceSheetData(
            total_assets=94629,
            total_liabilities=100,
            net_worth=94529,
        ))

    def test_structure(self):
        parsed = json.loads(render(self._report_with_bs()))
        bs = parsed["balanceSheet"]
        assert bs["totalAssets"] == 94629
        assert bs["totalLiabilities"] == 100
        assert bs["netWorth"] == 94529


class TestChartData:
    def test_chart_data_present(self):
        report = _minimal_report(chart_data=ChartData(
            net_worth_trend=[SnapshotPoint(date="2026-01-01", total=90000)],
            monthly_flows=[MonthlyFlowPoint(month="2026-01", income=10000, expenses=5000, savings_rate=50.0)],
        ))
        parsed = json.loads(render(report))
        assert parsed["chartData"]["netWorthTrend"] == [{"date": "2026-01-01", "total": 90000}]
        assert parsed["chartData"]["monthlyFlows"][0]["savingsRate"] == 50.0

    def test_chart_data_none(self):
        parsed = json.loads(render(_minimal_report()))
        assert parsed["chartData"] is None


class TestCashFlow:
    def test_cashflow_camel_case(self):
        report = _minimal_report(cashflow=CashFlowData(
            period="March 2026",
            income_items=[CashFlowItem(category="Salary", amount=10000, count=2)],
            total_income=10000, expense_items=[], total_expenses=0,
            net_cashflow=10000, invested=5000, credit_card_payments=1000,
            savings_rate=60.0, takehome_savings_rate=45.0,
        ))
        parsed = json.loads(render(report))
        cf = parsed["cashflow"]
        assert cf["incomeItems"][0]["category"] == "Salary"
        assert cf["totalIncome"] == 10000
        assert cf["netCashflow"] == 10000
        assert cf["creditCardPayments"] == 1000
        assert cf["takehomeSavingsRate"] == 45.0
